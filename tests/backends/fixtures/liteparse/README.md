# LiteParse fixture provenance

## Captured by

`lit parse` from **liteparse 2.16.0** on the polqa-re host, **2026-10-15**.

### items.json

```bash
lit parse tmp/cmp/p863_rec_p1-10.pdf \
  --format json --extract-text-metadata -o items.json -q
```

10 pages, 435 text_items total, one record per `text_items` entry: `text, x, y,
width, height, font_name, font_size, font_weight, confidence, ...`. The
`font_weight` field is present in the Python object but **absent from the
CLI's JSON dump**, so consumers reading the dump must fall back to
`"Bold" in font_name`.

### vec.json

```bash
lit parse tmp/cmp/p863_rec_p1-10.pdf \
  --format json --extract-vector-graphics --extract-text-metadata -o vec.json -q
```

10 pages, 406 lines + 313 shapes. `lines` is `[{x1, y1, x2, y2, stroke, fill,
fill_color}, ...]`, `shapes` is `[{bbox: {x, y, width, height, ...}, ...}]`.

### Why pin

Byte-identity of the acceptance tests depends on liteparse's line geometry not
changing. If liteparse changes its vector-graphics output, the mechanism
numbers in the table-region acceptance test (rule bands 54, full-width 48,
raw regions 42, ≥2-column regions 7 on the 10-page slice) will fail for
a reason that has nothing to do with the detector — re-record under the
new version and re-pin.
