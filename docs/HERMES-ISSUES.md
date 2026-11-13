# Hermes issues to open

Requested by the catalogue review of `hermes-music-studio` (2026-11-12). These are written here, not
opened, per the job instructions.

---

## Public image-generation call for plugins

**Title:** Plugin API for image generation (`ctx.image` or an `llm.image()` sibling)

**What we need**

A plugin can call `ctx.llm.complete(...)` for text, but there is no public equivalent for images. The
only working path is importing core internals — `tools.image_generation_tool._handle_image_generate`
and `agent.image_gen_provider.save_url_image` — which is private, unstable, and reads the provider
credentials of the host.

Music Studio used that to generate album cover art from the song's title and style. The catalogue rules
correctly require plugins to stay on public APIs, so the feature is removed in 1.0.0 rather than shipped
on a private import. Uploading your own cover image still works.

**Proposed shape**

```python
result = ctx.image.generate(prompt="album cover, moody night sky", aspect_ratio="1:1")
# -> object with .data (bytes) or .path, .mime_type, .provider, .model, .usage
```

Trust gates should mirror `ctx.llm`: the host owns provider/model selection and credentials, with the
same `plugins.entries.<id>.llm.allow_*_override`-style flags if overrides are ever needed.

**Why it matters beyond this plugin**

Any plugin with a visual output — cover art, thumbnails, diagram rendering — hits the same wall. Without
a public seam, each one either drops the feature or reaches into core.

**Interim behaviour in Music Studio 1.0.0**

`POST /studio/covers/{id}/generate` and the "Generate cover" control are gone; `POST /studio/covers/{id}`
(upload) remains. When a public call exists, the feature comes back behind it.
