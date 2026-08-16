# Published snapshots

The JSON files in this directory are generated, not written by hand. They are produced on the machine
that holds the instrument data:

```bash
python run.py publish --config config.toml
```

and committed from there. `index.json` is the manifest; one file per calendar year holds that year's
coverage, overlap segments, events, and verdicts.

They are committed deliberately: the static site must keep serving the last known record when the
data machine is offline. Their shape is fixed by [`../../docs/data-format.md`](../../docs/data-format.md).

Do not edit these files. A correction belongs in the source the record was built from — for a
hand-maintained record, the CSV named in `config.toml` — so that the next run does not silently undo
it.
