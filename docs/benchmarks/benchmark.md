# ParseCraft benchmark report

package: `2026.9.11.post9.dev0+917dbdb`

## Results

| document | backend | selected | elapsed_s | pages | pages/s | chunks | coverage | peak bytes | failures |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3f6bb9d6f78e0293b56acd4714dd68cb7d6d1d293402031ce9d5a216bcaf9d75-pg1342.txt | native-text | yes | 0.002439 | 1 | 409.936869 | paragraph=1 | 0.987946 | 3056449 | — |
| 43fad3e0ac5190a3b0bc6a41f7b1a853201a26ec2e6b74871f5d96239a8c34cf-commonmark-spec.md | native-markdown | yes | 0.739279 | 1 | 1.352669 | code=694,heading=45,list=27,paragraph=649,quote=5,unknown=2 | 0.716678 | 8536534 | — |
| cb459acca15a2aa895863eaeff6f1d5b9c4ca15578ae3e3706f757787a385969-rfc5322.txt | native-text | yes | 0.008576 | 1 | 116.600399 | paragraph=547 | 0.975859 | 1138802 | — |
| etsi-ts-103558.pdf | liteparse | yes | 6.010629 | 68 | 11.313292 | paragraph=68 | 1.011849 | 1206330 | — |
| etsi-ts-103558.pdf | native-pdf | no | 6.467704 | 68 | 10.513778 | paragraph=337 | 0.963069 | 17810564 | — |
| fc63bcd61715d0181dd8e85998b1e6201ae3515fc6626102101cab1841e11ec6-nist-sp-800-53r5.pdf | liteparse | yes | 115.646923 | 492 | 4.254329 | paragraph=492 | 0.904040 | 3071007 | — |
| fc63bcd61715d0181dd8e85998b1e6201ae3515fc6626102101cab1841e11ec6-nist-sp-800-53r5.pdf | native-pdf | no | 4.002183 | 492 | 122.932906 | paragraph=1768 | 0.994717 | 7729439 | — |
| itu-t-p863.pdf | liteparse | - | 5.672197 | 80 | 14.103882 | paragraph=80 | 1.017026 | 652061 | — |
| itu-t-p863.pdf | native-pdf | - | 0.611496 | 80 | 130.826715 | paragraph=198 | 0.998596 | 1113373 | — |
| whatwg-html.html | native-html | yes | 0.872653 | 1 | 1.145931 | heading=3,list=196,paragraph=1 | 0.271030 | 692279 | — |
| wikipedia-calculus.html | native-html | yes | 2.459948 | 1 | 0.406513 | heading=33,list=35,paragraph=119,quote=1,table=15 | 0.106438 | 2459150 | — |

## Skips

| document | reason |
| --- | --- |
| exoplanets.csv | unsupported source suffix '.csv' |
| wikidata-q42.json | unsupported source suffix '.json' |
