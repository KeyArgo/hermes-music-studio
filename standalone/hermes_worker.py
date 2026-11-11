"""Runs in Hermes's own Python environment; exchanges JSON over stdin/stdout."""
import contextlib
import base64
import io
import json
import os
from pathlib import Path
import sys

def main():
    request = json.load(sys.stdin)
    repo = Path(os.environ.get('HERMES_MUSIC_HERMES_REPO', Path.home() / '.hermes' / 'hermes-agent')).expanduser()
    sys.path.insert(0, str(repo))
    # Hermes manages its own Python packages and configured provider credentials.
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        import hermes_bootstrap  # noqa: F401
        if request.get('action') == 'models':
            from hermes_cli.inventory import build_model_options_payload, load_picker_context
            data = build_model_options_payload(load_picker_context(), include_unconfigured=False)
            result = {'provider': data.get('provider'), 'model': data.get('model'),
                      'providers': [{'slug': p.get('slug'), 'name': p.get('name'),
                                     'models': p.get('models', [])}
                                    for p in data.get('providers', [])]}
        elif request.get('action') == 'complete':
            from hermes_cli.runtime_provider import resolve_runtime_provider
            from run_agent import AIAgent
            body = request['request']
            runtime = resolve_runtime_provider(requested=body['provider'], target_model=body['model'])
            kwargs = {k: runtime.get(k) for k in ('provider','api_mode','base_url','api_key','credential_pool','command','args')}
            agent = AIAgent(model=body['model'], **kwargs, enabled_toolsets=[], quiet_mode=True,
                            skip_context_files=True, skip_memory=True, skip_background_review=True,
                            max_tokens=body['max_tokens'], max_iterations=1, run_budget_seconds=120)
            if getattr(agent, 'tools', []):
                raise RuntimeError('Hermes did not disable tools for the writing assistant')
            messages = body['messages']
            system = messages[0]['content']
            conversation = '\n\n'.join(f"{m['role'].upper()}: {m['content']}" for m in messages[1:])
            prompt = conversation + ('\n\nNow produce the requested draft.' if 'JSON object' in system else '')
            answer = agent.run_conversation(prompt, system_message=system)
            content = answer.get('final_response', '')
            if not isinstance(content, str) or not content.strip():
                raise RuntimeError('Hermes returned an empty response')
            result = {'content': content}
        elif request.get('action') == 'art':
            from tools.image_generation_tool import _handle_image_generate
            from agent.image_gen_provider import save_url_image
            from agent.provider_media import cache_dir
            from PIL import Image
            raw = _handle_image_generate({'prompt': request['prompt'], 'aspect_ratio':'1:1'})
            answer = json.loads(raw) if isinstance(raw, str) else raw
            image = answer.get('image') if isinstance(answer, dict) and answer.get('success') else None
            if not isinstance(image, str) or not image:
                raise RuntimeError('Hermes image generation is unavailable')
            if image.startswith(('https://', 'http://')):
                image = str(save_url_image(image, prefix='music-cover', max_bytes=12 * 1024 * 1024))
            path = Path(image).resolve(strict=True)
            cache = cache_dir('generated/images').resolve()
            if not path.is_relative_to(cache) or path.stat().st_size > 12 * 1024 * 1024:
                raise RuntimeError('Hermes image was not in its generated-image cache')
            with Image.open(path) as picture:
                if picture.width * picture.height > 20_000_000:
                    raise RuntimeError('image is too large')
                picture.thumbnail((1024, 1024))
                picture = picture.convert('RGB')
                buffer = io.BytesIO()
                picture.save(buffer, format='JPEG', quality=88)
            binary = buffer.getvalue()
            if not binary or len(binary) > 2 * 1024 * 1024:
                raise RuntimeError('image could not be prepared')
            result = {'mime': 'image/jpeg', 'data': base64.b64encode(binary).decode('ascii')}
        else:
            raise ValueError('unsupported request')
    print(json.dumps(result, ensure_ascii=False))

if __name__ == '__main__':
    try:
        main()
    except Exception:
        print(json.dumps({'error': 'Hermes could not complete this request. Check the selected provider and model.'}))
        sys.exit(1)
