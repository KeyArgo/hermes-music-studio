## Summary

What changed and why.

## Tests run

- [ ] `env -u PYTHONPATH TMPDIR=/tmp python3 -m pytest --rootdir=tests tests -q`
- [ ] `node --test tests/*.mjs`
- [ ] Manual check (describe it, if any)

Paste the final summary line from each suite.

## Checklist

- [ ] A test fails without this change.
- [ ] No new runtime dependency in the plugin.
- [ ] `LIMITS.md` updated if the change narrows what the studio can promise.
- [ ] `CHANGELOG.md` updated for a user-visible change.
- [ ] README counts, `TESTING.md` and `VERIFY.md` agree if a test count changed.
- [ ] No personal data, paths, email addresses or secrets in the diff.
