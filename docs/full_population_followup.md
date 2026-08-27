# Lite To Full Continuation

The user approved an unattended continuation on 2026-08-28. Full means the
official 100M tier, not a Lite prefix. Existing Lite work must finish and pass
verification before Full inference starts. This does not authorize writes to
shared training data, inventory expansion, adaptive T5 batching, or bulk data
transfer through the desktop.

## Runtime Preflight Incident

A read-only metadata probe incorrectly assumed that a shared virtualenv's
interpreter was executable in a different container image. It was a broken
symlink there. This occurred before Full inference or output creation. The
corrected diagnostic checks interpreter existence and imports inside each
target container, without changing its environment. Storage copy operations
use a verified standard-library interpreter. Parquet work and inference use
the separately verified compute runtime. Formal continuation preflight must
reject missing interpreters/imports, never silently replace the inference
environment. Detailed deployment locators and incident evidence are kept in
local operational records, not in this portable usage document.
