# Publicando no PyPI

Para mantenedores. Depois de publicado, qualquer pessoa usa com `uv add obskitpy`, `pip install obskitpy`
ou `uvx obskitpy`. O workflow [`publish.yml`](https://github.com/pedrowill-dev/obskitpy/blob/main/.github/workflows/publish.yml) usa
[Trusted Publishing](https://docs.pypi.org/trusted-publishers/) (OIDC): **não existe token guardado** no GitHub.

## Configuração única

1. Crie a conta em https://pypi.org (com 2FA) e abra **Your projects → Publishing** (ou
   https://pypi.org/manage/account/publishing/).
2. Em **Add a new pending publisher**, preencha:

   | Campo | Valor |
   |---|---|
   | PyPI Project Name | `obskitpy` |
   | Owner | `pedrowill-dev` |
   | Repository name | `obskitpy` |
   | Workflow name | `publish.yml` |
   | Environment name | `pypi` |

3. No GitHub, em **Settings → Environments**, crie o ambiente `pypi` (opcional: marque *Required reviewers*
   para exigir uma aprovação manual antes de cada publicação).

## Publicar uma versão

```bash
# 1. suba a versão em obskitpy/pyproject.toml (ex.: 0.1.1) e faça commit
# 2. crie e envie a tag
git tag v0.1.1 && git push origin v0.1.1
```

O workflow compila (`uv build`), e publica no PyPI. Acompanhe em *Actions*. Não é possível republicar
a mesma versão: se errar, suba o número e publique de novo.

## Testar antes (opcional)

```bash
cd obskitpy && uv build && uvx twine check dist/*
uv run --with dist/obskitpy-*.whl --no-project obskitpy --help
```

Para ensaiar no TestPyPI, crie um publisher equivalente em https://test.pypi.org e adicione
`repository-url: https://test.pypi.org/legacy/` ao passo `pypa/gh-action-pypi-publish`.
