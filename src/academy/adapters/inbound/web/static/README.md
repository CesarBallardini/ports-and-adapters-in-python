# Vendored front-end assets

One file, committed rather than fetched, because ADR-0011 says the repository stays clonable and
runnable with no build step and no npm — and a CDN link would quietly add "and an internet
connection" to that list, in the request path of every page.

## `htmx.min.js`

| | |
|---|---|
| Version | **2.0.7** |
| Source | `https://cdn.jsdelivr.net/npm/htmx.org@2.0.7/dist/htmx.min.js` |
| Size | 51 076 bytes |
| SHA-256 | `60231ae6ba9db3825eb15a261122d5f55921c4d53b66bf637dc18b4ee27c79f9` |
| SRI | `sha384-ZBXiYtYQ6hJ2Y0ZNoYuI+Nq5MqWBr+chMrS/RkXpNzQCApHEhOt2aY8EJgqwHLkJ` |
| Licence | Zero-Clause BSD |

### Upgrading

```bash
curl -sSL -o src/academy/adapters/inbound/web/static/htmx.min.js \
  https://cdn.jsdelivr.net/npm/htmx.org@<version>/dist/htmx.min.js
sha256sum src/academy/adapters/inbound/web/static/htmx.min.js
```

Then update the table above, and **re-check the default `responseHandling` rules** in the new
file:

```bash
grep -o 'responseHandling:\[.\{0,120\}' src/academy/adapters/inbound/web/static/htmx.min.js
```

2.0.7 ships `[{code:"204",swap:false},{code:"[23]..",swap:true},{code:"[45]..",swap:false,error:true}]`
— a 4xx response is **not** swapped into the page. The adapter overrides that in exactly one
place, the `htmx-config` meta tag in `templates/base.html`, so an honest 403 or 422 from the error
boundary is still shown to the person who caused it. If a future release changes those defaults or
renames the key, that meta tag is what stops working, and
`tests/adapters/test_web_rendering.py::test_the_swap_rule_is_configured_for_error_responses`
is what says so.

### Why this directory is excluded from the formatting hooks

`.pre-commit-config.yaml` excludes it from `end-of-file-fixer`, `trailing-whitespace` and
`mixed-line-ending`. A minified bundle is one very long line that no hook here should be
rewriting: an edit would silently change a file whose whole value is being byte-identical to the
published release the checksum above names.
