# Local Snow Owl with the SNOMED CT Uzbekistan Edition

Validated 2026-10-29 with Snow Owl OSS 10.1.0 and `SnomedCT_UzbekistanSimplexEdition_Production_20260928T120000Z.zip`.

Needs Docker, about 16 GB free RAM (6 GB heap each for Snow Owl and Elasticsearch) and about 7 GB disk.

## 1. Get the release

SNOMED CT is licensed content and is not included in this repo. Get `SnomedCT_UzbekistanSimplexEdition_Production_20260928T120000Z.zip` from [MLDS](https://mlds.ihtsdotools.org/) or ask the Uzbekistan SNOMED CT National Release Center. It is a full edition (International plus the Uzbekistan module), so nothing else needs importing.

## 2. Get this repo

```sh
git clone https://github.com/vadi2/snowowl-uzbekistan.git && cd snowowl-uzbekistan
```

Put the release zip in this folder.

## 3. Start

```sh
docker compose up -d
curl -u test:test http://localhost:8080/snowowl/info   # wait until it answers; login is test/test
```

## 4. Create the code system

```sh
curl -u test:test -X POST -H 'Content-Type: application/json' http://localhost:8080/snowowl/codesystems -d '{
  "id": "SNOMEDCT-UZ", "url": "http://snomed.info/sct/11000339101",
  "title": "SNOMED CT Uzbekistan Edition", "status": "active", "toolingId": "snomed",
  "oid": "2.16.840.1.113883.6.96",
  "settings": {
    "moduleIds": ["11000339101", "900000000000207008", "900000000000012004"],
    "locales": ["uz-x-21000339107", "en-x-900000000000509007", "en-x-900000000000508004"],
    "languages": [
      {"languageTag": "uz", "languageRefSetIds": ["21000339107"]},
      {"languageTag": "en", "languageRefSetIds": ["900000000000509007", "900000000000508004"]},
      {"languageTag": "en-us", "languageRefSetIds": ["900000000000509007"]},
      {"languageTag": "en-gb", "languageRefSetIds": ["900000000000508004"]}
    ],
    "publisher": "SNOMED International", "maintainerType": "SNOMED_INTERNATIONAL"
  }}'
```

## 5. Import (10-15 minutes)

```sh
curl -sS -i -u test:test "http://localhost:8080/snowowl/snomedct/SNOMEDCT-UZ/import?type=snapshot&createVersions=true" \
  -F file=@SnomedCT_UzbekistanSimplexEdition_Production_20260928T120000Z.zip | grep -i '^location'
# poll the printed Location URL until "status" is "FINISHED" and "response.success" is true
curl -u test:test '<Location URL>?pretty'
```

A handful of "GuideURL field was empty" MRCM warnings in `defects` are harmless.

## 6. Expand the IG value sets

Snow Owl OSS can't load FHIR ValueSets, so `expand-snomed-valuesets.py` does the expansion. It downloads the latest `uz.dhp.core` and `uz.dhp.integrations` packages from packages.fhir.org, picks the value sets that use only SNOMED CT, evaluates their `compose` against Snow Owl and writes one ValueSet with an `expansion` per value set:

```sh
./expand-snomed-valuesets.py                      # writes expansions/<package>/ValueSet-<id>.json
./expand-snomed-valuesets.py uz.dhp.core#0.12.0   # one package, pinned version
```

Takes about 2 minutes. It prints the code count per value set and flags listed codes that are inactive or unknown in the release. Value sets that include all of SNOMED CT are skipped. `display` is the US English preferred term; a `designation` with `language: uz` is added where the release has an Uzbek term.

### Single value sets with curl

Rule-based value sets (an `is-a` filter):

```sh
VS=body-site-vs   # or condition-severity-vs, target-disease-vs, adverse-event-outcome-vs, goal-description-vs, recommendation-reason-vs, reaction-type-vs
ROOT=$(curl -s https://dhp.uz/fhir/core/ValueSet-$VS.json | jq -r '.compose.include[0].filter[0].value')   # concept in the value set's is-a filter, e.g. 123037004
curl -u test:test -H 'Accept: application/fhir+json' -G 'http://localhost:8080/snowowl/fhir/ValueSet/$expand' \
  --data-urlencode "url=http://snomed.info/sct/11000339101?fhir_vs=isa/$ROOT" \
  --data-urlencode 'count=200000' -o $VS.json
```

Value sets that list their codes, with each code's display (add `&displayLanguage=uz` for Uzbek; it returns the code when there is no Uzbek term). Use `https://dhp.uz/fhir/integrations/...` for integrations value sets:

```sh
curl -s https://dhp.uz/fhir/core/ValueSet-blood-group-vs.json | jq -r '.compose.include[].concept[].code' |
while read c; do
  curl -s -u test:test -H 'Accept: application/fhir+json' \
    "http://localhost:8080/snowowl/fhir/CodeSystem/\$lookup?system=http://snomed.info/sct&version=http://snomed.info/sct/11000339101&code=$c" |
    jq -r --arg c "$c" '[$c, (.parameter[] | select(.name=="display") | .valueString)] | @csv'
done
```

To get one value set as CSV:

```sh
jq -r '.expansion.contains[] | [.code, .display, (.designation[0].value // "")] | @csv' \
  expansions/uz.dhp.core/ValueSet-body-site-vs.json > body-site-vs.csv
```

## Stop, start, reset

```sh
docker compose down      # stop, keeps the imported data
docker compose up -d     # start again, no re-import needed
docker compose down -v   # stop and delete all data
```
