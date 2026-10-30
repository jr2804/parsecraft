# ParseCraft benchmark report

package: `2026.10.13.post53.dev0+a546d73`

## Results

| document | backend | selected | elapsed_s | pages | pages/s | chunks | coverage | peak bytes | failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3f6bb9d6f78e0293b56acd4714dd68cb7d6d1d293402031ce9d5a216bcaf9d75-pg1342.txt | native-text | yes | 0.005146 | 1 | 194.314361 | paragraph=1 | 0.987946 | 3059545 | — |
| 43fad3e0ac5190a3b0bc6a41f7b1a853201a26ec2e6b74871f5d96239a8c34cf-commonmark-spec.md | native-markdown | yes | 0.682634 | 1 | 1.464914 | code=694,heading=45,list=98,paragraph=648,quote=5,unknown=2 | 0.716581 | 8752856 | — |
| 98977d783f46449f7aba89c5c8a8348b981cf55938fa9ec7d71009e6d4fa9770-etsi-ts-103558.pdf | liteparse | yes | 2.700979 | 68 | 25.176054 | heading=253,paragraph=524 | 0.925388 | 3193198 | — |
| 98977d783f46449f7aba89c5c8a8348b981cf55938fa9ec7d71009e6d4fa9770-etsi-ts-103558.pdf | pdf-inspector | no | 0.368981 | 68 | 184.291382 | paragraph=62 | 0.883741 | 558756 | — |
| 98977d783f46449f7aba89c5c8a8348b981cf55938fa9ec7d71009e6d4fa9770-etsi-ts-103558.pdf | pymupdf | no | 0.006807 | 0 | - | - | 0.000000 | 144683 | dependency_missing |
| cb459acca15a2aa895863eaeff6f1d5b9c4ca15578ae3e3706f757787a385969-rfc5322.txt | native-text | yes | 0.012893 | 1 | 77.562069 | paragraph=547 | 0.975859 | 1151898 | — |
| exoplanets.csv | native-csv | yes | 0.002143 | 1 | 466.613779 | table=1 | 1.211870 | 106925 | — |
| f19653a061f3506ddfc2bf2801e6a6a57f8435177d9b992be860322dc63e2418-itu-t-p863.pdf | liteparse | yes | 3.013789 | 80 | 26.544659 | heading=225,paragraph=315 | 0.978399 | 1973536 | — |
| f19653a061f3506ddfc2bf2801e6a6a57f8435177d9b992be860322dc63e2418-itu-t-p863.pdf | pdf-inspector | no | 4.300209 | 80 | 18.603746 | paragraph=78 | 0.982063 | 543397 | — |
| f19653a061f3506ddfc2bf2801e6a6a57f8435177d9b992be860322dc63e2418-itu-t-p863.pdf | pymupdf | no | 0.004855 | 0 | - | - | 0.000000 | 137721 | dependency_missing |
| fc63bcd61715d0181dd8e85998b1e6201ae3515fc6626102101cab1841e11ec6-nist-sp-800-53r5.pdf | liteparse | yes | 65.342901 | 492 | 7.529510 | heading=3522,paragraph=3259 | 0.877092 | 16451000 | — |
| fc63bcd61715d0181dd8e85998b1e6201ae3515fc6626102101cab1841e11ec6-nist-sp-800-53r5.pdf | pdf-inspector | no | 6.862530 | 492 | 71.693673 | paragraph=492 | 1.010419 | 3685628 | — |
| fc63bcd61715d0181dd8e85998b1e6201ae3515fc6626102101cab1841e11ec6-nist-sp-800-53r5.pdf | pymupdf | no | 0.003641 | 0 | - | - | 0.000000 | 137465 | dependency_missing |
| whatwg-html.html | native-html | yes | 0.166028 | 1 | 6.023066 | heading=3,list=196,paragraph=1 | 0.271130 | 698844 | — |
| whatwg-html.html | trafilatura | no | 4.940699 | 1 | 0.202400 | paragraph=371 | 0.020688 | 36698968 | — |
| wikipedia-calculus.html | native-html | yes | 0.473522 | 1 | 2.111833 | heading=33,list=35,paragraph=119,quote=1,table=15 | 0.107011 | 2445743 | — |
| wikipedia-calculus.html | trafilatura | no | 1.089451 | 1 | 0.917894 | heading=32,list=127,paragraph=74,table=1 | 0.073228 | 4877183 | — |

## Skips

| document | reason |
| --- | --- |
| wikidata-q42.json | unsupported source suffix '.json' |
