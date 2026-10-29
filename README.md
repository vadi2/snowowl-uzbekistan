# Local Snow Owl with the SNOMED CT Uzbekistan Edition

Validated 2026-10-29 with Snow Owl OSS 10.1.0 and `SnomedCT_UzbekistanSimplexEdition_Production_20260928T120000Z.zip`.

Needs Docker, about 16 GB free RAM (6 GB heap each for Snow Owl and Elasticsearch) and about 7 GB disk.

## 1. Get the release

SNOMED CT is licensed content and is not included in this repo. Get `SnomedCT_UzbekistanSimplexEdition_Production_20260928T120000Z.zip` (583 MB) from [MLDS](https://mlds.ihtsdotools.org/) or ask the Uzbekistan SNOMED CT National Release Center and put it in this folder. It is a full edition: International 20260901 plus the Uzbekistan module `11000339101`, so nothing else needs importing.

## 2. Config and compose file

Take the config folder from the Snow Owl repo. Its `docker-compose.yml` is broken (undefined `es-data` volume), so use the one below instead.

```sh
git clone --depth 1 https://github.com/b2ihealthcare/snow-owl.git /tmp/snow-owl
cp -r /tmp/snow-owl/docker/compose/config . && rm -rf /tmp/snow-owl
```

`docker-compose.yml`:

```yaml
services:
  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.19.15
    container_name: snowowl-es
    environment:
      - "ES_JAVA_OPTS=-Xms6g -Xmx6g"
    ulimits:
      memlock: { soft: -1, hard: -1 }
      nofile: { soft: 65536, hard: 65536 }
    volumes:
      - es-data:/usr/share/elasticsearch/data
      - ./config/elasticsearch/elasticsearch.yml:/usr/share/elasticsearch/config/elasticsearch.yml
      - ./config/elasticsearch/synonym.txt:/usr/share/elasticsearch/config/analysis/synonym.txt
    healthcheck:
      test: curl --fail http://localhost:9200/_cluster/health?wait_for_status=green || exit 1
      interval: 2s
      timeout: 2s
      retries: 90
    restart: unless-stopped
  snowowl:
    image: b2ihealthcare/snow-owl-oss:10.1.0
    container_name: snowowl
    environment:
      - "SO_JAVA_OPTS=-Xms6g -Xmx6g"
      - "ELASTICSEARCH_URL=http://elasticsearch:9200"
    depends_on:
      elasticsearch:
        condition: service_healthy
    volumes:
      - ./config/snowowl/snowowl.yml:/etc/snowowl/snowowl.yml
      - ./config/snowowl/users:/etc/snowowl/users
      - so-data:/var/lib/snowowl/resources
    ports:
      - "127.0.0.1:8080:8080"
    restart: unless-stopped
volumes:
  es-data:
  so-data:
```

## 3. Start

```sh
docker compose up -d
curl -u test:test http://localhost:8080/snowowl/info   # wait until it answers; login is test/test
```

If Elasticsearch fails on startup, run `sudo sysctl -w vm.max_map_count=262144`.

## 4. Create the code system

`21000339107` is the Uzbek language refset in this release.

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

## 5. Import (about 15 minutes)

```sh
curl -i -u test:test "http://localhost:8080/snowowl/snomedct/SNOMEDCT-UZ/import?type=snapshot&createVersions=true" \
  -F file=@SnomedCT_UzbekistanSimplexEdition_Production_20260928T120000Z.zip
# poll the Location header URL until "status": "FINISHED" and "success": true
curl -u test:test '<Location URL>?pretty'
```

A handful of "GuideURL field was empty" MRCM warnings in `defects` are harmless.

## 6. Expand the IG value sets

Snow Owl OSS can't load FHIR ValueSets (`$load-package` imports nothing and creating one returns "FHIR ValueSet resource creation is not configured"), so `expand-snomed-valuesets.py` does it instead. It downloads the latest `uz.dhp.core` and `uz.dhp.integrations` packages from packages.fhir.org, picks the value sets that use only SNOMED CT, evaluates their `compose` against Snow Owl and writes one ValueSet with an `expansion` per value set:

```sh
./expand-snomed-valuesets.py                      # writes expansions/<package>/ValueSet-<id>.json
./expand-snomed-valuesets.py uz.dhp.core#0.12.0   # one package, pinned version
```

Takes about 2 minutes for both IGs (71 value sets on 2026-10-29). It prints the code count per value set and flags listed codes that are inactive or unknown in the release. Value sets that include all of SNOMED CT are skipped.

To get one value set as CSV:

```sh
jq -r '.expansion.contains[] | [.code, .display, (.designation[0].value // "")] | @csv' \
  expansions/uz.dhp.core/ValueSet-body-site-vs.json > body-site-vs.csv
```

Notes on the script output:

- `display` is the US English preferred term. A `designation` with `language: uz` is added where the release has an Uzbek term; it has only 11 Uzbek descriptions on 10 concepts so far.

### Single value sets with curl

Rule-based value sets (an `is-a` filter) expand through Snow Owl's implicit value set for that concept. Read the concept from the published value set, then expand it in one call (`count` must be at least the size of the set):

```sh
VS=body-site-vs   # or condition-severity-vs, target-disease-vs, adverse-event-outcome-vs, goal-description-vs, recommendation-reason-vs, reaction-type-vs
ROOT=$(curl -s https://dhp.uz/fhir/core/ValueSet-$VS.json | jq -r '.compose.include[0].filter[0].value')   # concept in the value set's is-a filter, e.g. 123037004
curl -u test:test -H 'Accept: application/fhir+json' -G 'http://localhost:8080/snowowl/fhir/ValueSet/$expand' \
  --data-urlencode "url=http://snomed.info/sct/11000339101?fhir_vs=isa/$ROOT" \
  --data-urlencode 'count=200000' -o $VS.json
```

| Value set | Concept |
|---|---|
| body-site-vs | 123037004 Body structure |
| condition-severity-vs | 272141005 Severities |
| target-disease-vs | 64572001 Disease |
| adverse-event-outcome-vs | 281647001 Adverse reaction |
| goal-description-vs, recommendation-reason-vs, reaction-type-vs | 404684003 Clinical finding |

Value sets that list their codes need no expansion; look up each code for its display (add `&displayLanguage=uz` for Uzbek, which returns the code itself when there is no Uzbek term). Use `https://dhp.uz/fhir/integrations/...` for integrations value sets:

```sh
curl -s https://dhp.uz/fhir/core/ValueSet-blood-group-vs.json | jq -r '.compose.include[].concept[].code' |
while read c; do
  curl -s -u test:test -H 'Accept: application/fhir+json' \
    "http://localhost:8080/snowowl/fhir/CodeSystem/\$lookup?system=http://snomed.info/sct&version=http://snomed.info/sct/11000339101&code=$c" |
    jq -r --arg c "$c" '[$c, (.parameter[] | select(.name=="display") | .valueString)] | @csv'
done
```

## Stop, start, reset

```sh
docker compose down      # stop, keeps the imported data
docker compose up -d     # start again, no re-import needed
docker compose down -v   # stop and delete all data
```
