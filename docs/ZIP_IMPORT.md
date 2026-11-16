# ZIP intake contract

`POST /jobs/import-zips` accepts no body. It scans `/files/inbox` (host `data/inbox`)
for completed `.zip` files. It never renders, sends Telegram messages or uploads.
The response has `imported` and `duplicates` arrays of job IDs, `failed` objects
with `file` and `error`, and a `deferred` array of filenames still being copied.

A package contains `manifest.json` and the images named in its scenes, either at
the ZIP root or inside one common folder. Optional `READY` is ignored and recreated.
There must be at least three scenes/images; the count is chosen to suit the content,
not fixed at four. There is no eight-scene limit. Limit: 100 MB compressed,
200 MB expanded, 256 archive entries, 1 MB manifest. Linked/encrypted files, traversal,
duplicate paths, extra files and missing images are rejected.
PNG chunk lengths, checksums and the end marker are checked before import/render,
so truncated PNGs fail with the filename instead of hanging FFmpeg's image loop.

Jobs use `zip-<24 hex characters of ZIP SHA-256>` as their ID. Importing the exact
same ZIP again preserves the existing job and status. Imported ZIPs move into
`processed/`; invalid ZIPs move into `failed/` beside a `.zip.error.txt` explanation.
ZIPs with an incomplete central directory and modified within 60 seconds are
left in place for the next run. Repacking changes the ZIP digest and may create
a new job; do not repackage an already uploaded Short unless you intend a new job.

The workflow connects both input triggers to `Nhap ZIP tu inbox`, then to the
existing `Lay job moi`. Folder-based jobs still work. ZIP contents are extracted
internally into a staging folder and promoted only after validation; the user
does not extract them manually. Telegram approval and YouTube privacy remain unchanged.
