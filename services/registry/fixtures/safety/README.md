# Brand-safety fixtures

Paired fixtures for `services/registry/safety_suite.py`. Each seeded file
carries a known defect; each clean file must scan cleanly. Both halves
matter: a checker that only ever sees bad input cannot show it has no false
positives.

| File | Expected codes |
|---|---|
| `violations/seeded-shortener-and-us-spelling.md` | `link-shortener`, `sa-english-spelling` |
| `violations/no-cta.md` | `missing-cta` |
| `violations/non-utm-url.md` | `url-utm` |
| `violations/canvas-url-missing-utm.md` | `url-utm` |
| `violations/unsupported-claim.md` | `unsupported-claim` |
| `clean/control.md` | none |
| `clean/supported-claim.md` | none |

Run the paired assertions with:

```sh
python services/registry/test_safety_suite.py
```
