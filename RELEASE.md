# Release Checklist — v0.5.0

## Pre-flight

- [x] 227 Python tests pass
- [x] 124 TypeScript tests pass (incl. live OpenAI/Anthropic/Google)
- [x] All 4 package manifests have valid metadata (homepage, repo, bugs, license)
- [x] `wickd-core` has a README and is listed in `files`
- [x] `wickd` depends on `wickd-core@^0.5.0` (not `*`)
- [x] `wickd-proxy` has a valid `setuptools.build_meta` backend
- [x] All 4 distributables build locally:
  - `packages/sdk-python/dist/wickd_ai-0.5.0-py3-none-any.whl`
  - `packages/sdk-python/dist/wickd_ai-0.5.0.tar.gz`
  - `packages/proxy/dist/wickd_proxy-0.5.0-py3-none-any.whl`
  - `packages/proxy/dist/wickd_proxy-0.5.0.tar.gz`
  - `packages/core/wickd-core-0.5.0.tgz` (via `npm pack`)
  - `packages/sdk-typescript/wickd-0.5.0.tgz` (via `npm pack`)

## Publish order (critical — `wickd-core` must go first)

1. `cd packages/core && npm publish --access public`
2. `cd packages/sdk-typescript && npm publish --access public`
3. `cd packages/sdk-python && twine upload dist/*` (or GH Action via tag push)
4. `cd packages/proxy && twine upload dist/*`

## Automated path (recommended)

```bash
git tag v0.5.0
git push origin v0.5.0
```

GH Action `publish.yml` handles everything: runs tests, publishes `wickd-core` + `wickd` to npm, publishes `wickd-ai` + `wickd-proxy` to PyPI.

Requires secrets:
- `NPM_TOKEN` — npm automation token with publish scope for both packages
- PyPI uses trusted publishing (OIDC) via `pypa/gh-action-pypi-publish` — configure trusted publisher for both `wickd-ai` and `wickd-proxy` on PyPI.

## Post-publish smoke test

```bash
# Clean env — npm
mkdir /tmp/wickd-test && cd /tmp/wickd-test
npm init -y && npm install wickd openai
node -e "const {agent,Budget}=require('wickd');console.log(typeof agent)"

# Clean env — pip
python -m venv .venv && . .venv/bin/activate
pip install wickd-ai[openai]
python -c "import wickd; print(wickd.__version__ if hasattr(wickd,'__version__') else 'imported')"
```

## Launch announcement draft

See `LAUNCH.md`.
