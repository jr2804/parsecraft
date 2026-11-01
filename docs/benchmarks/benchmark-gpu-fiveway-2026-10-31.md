# ParseCraft benchmark report

package: `2026.10.16.post8.dev0+9cbb645`

## Results

| document | backend | selected | elapsed_s | pages | pages/s | chunks | coverage | peak bytes | failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| etsi-ts-103558-p1-2.pdf | liteparse | yes | 0.232080 | 2 | 8.617718 | heading=7,paragraph=13 | 0.966726 | 1016000 | — |
| etsi-ts-103558-p1-2.pdf | ocr-ovis | no | 1391.588772 | 0 | - | - | 0.000000 | 757031641 | backend_error; backend_error |
| etsi-ts-103558-p1-2.pdf | ocr-qianfan | no | 939.408211 | 2 | 0.002129 | paragraph=2 | 1.002971 | 3716664895 | — |
| etsi-ts-103558-p1-2.pdf | ocr-surya | no | 1080.750753 | 0 | - | - | 0.000000 | 196043146 | backend_error; backend_error |
| etsi-ts-103558-p1-2.pdf | ocr-tele | no | 45.263506 | 0 | - | - | 0.000000 | 318631752 | backend_error; backend_error |
| etsi-ts-103558-p1-2.pdf | ocr-unlimited | no | 434.422893 | 1 | 0.002302 | paragraph=1 | 2.804516 | 698669143 | backend_error |
| etsi-ts-103558-p1-2.pdf | pdf-inspector | no | 0.247051 | 2 | 8.095485 | paragraph=2 | 1.042484 | 87545 | — |
| etsi-ts-103558-p1-2.pdf | pymupdf | no | 7.188551 | 2 | 0.278220 | paragraph=22 | 0.968212 | 16982944 | — |
| itu-t-p863-p1-2.pdf | liteparse | yes | 0.136741 | 2 | 14.626159 | heading=6,paragraph=7 | 0.939284 | 148522 | — |
| itu-t-p863-p1-2.pdf | ocr-ovis | no | 1099.364928 | 0 | - | - | 0.000000 | 589909488 | backend_error; backend_error |
| itu-t-p863-p1-2.pdf | ocr-qianfan | no | 762.049636 | 2 | 0.002625 | paragraph=2 | 1.318761 | 3715697375 | — |
| itu-t-p863-p1-2.pdf | ocr-surya | no | 932.948529 | 0 | - | - | 0.000000 | 195994953 | backend_error; backend_error |
| itu-t-p863-p1-2.pdf | ocr-tele | no | 59.544998 | 0 | - | - | 0.000000 | 327903208 | backend_error; backend_error |
| itu-t-p863-p1-2.pdf | ocr-unlimited | no | 11706.258197 | 1 | 0.000085 | paragraph=1 | 39.054645 | 3069146279 | backend_error |
| itu-t-p863-p1-2.pdf | pdf-inspector | no | 0.021635 | 2 | 92.441946 | paragraph=2 | 1.003643 | 8239 | — |
| itu-t-p863-p1-2.pdf | pymupdf | no | 0.046282 | 2 | 43.213531 | paragraph=10 | 0.942927 | 354151 | — |
| nist-sp-800-53r5-p1-2.pdf | liteparse | yes | 0.323639 | 2 | 6.179717 | heading=11,paragraph=4 | 0.747027 | 72406 | — |
| nist-sp-800-53r5-p1-2.pdf | ocr-ovis | no | 875.021666 | 0 | - | - | 0.000000 | 590921432 | backend_error; backend_error |
| nist-sp-800-53r5-p1-2.pdf | ocr-qianfan | no | 689.371411 | 2 | 0.002901 | paragraph=2 | 1.008649 | 3713827708 | — |
| nist-sp-800-53r5-p1-2.pdf | ocr-surya | no | 899.656743 | 0 | - | - | 0.000000 | 196495367 | backend_error; backend_error |
| nist-sp-800-53r5-p1-2.pdf | ocr-tele | no | 119.003536 | 0 | - | - | 0.000000 | 318124082 | backend_error; backend_error |
| nist-sp-800-53r5-p1-2.pdf | ocr-unlimited | no | 218.963487 | 1 | 0.004567 | paragraph=1 | 4.789189 | 3068728884 | backend_error |
| nist-sp-800-53r5-p1-2.pdf | pdf-inspector | no | 0.017760 | 2 | 112.614515 | paragraph=2 | 0.915676 | 7425 | — |
| nist-sp-800-53r5-p1-2.pdf | pymupdf | no | 0.028128 | 2 | 71.104285 | paragraph=11 | 0.752432 | 215831 | — |
