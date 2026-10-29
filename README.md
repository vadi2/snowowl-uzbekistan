# Local Snow Owl with the SNOMED CT Uzbekistan Edition

Validated 2026-10-29 with Snow Owl OSS 10.1.0 and `SnomedCT_UzbekistanSimplexEdition_Production_20260928T120000Z.zip`.

Needs Docker, about 16 GB free RAM (6 GB heap each for Snow Owl and Elasticsearch) and about 7 GB disk.

## 1. Get the release

SNOMED CT is licensed content and is not included in this repo. Get `SnomedCT_UzbekistanSimplexEdition_Production_20260928T120000Z.zip` (583 MB) from SNOMED International ([MLDS](https://mlds.ihtsdotools.org/)) under your own affiliate licence and put it in this folder. It is a full edition: International 20260901 plus the Uzbekistan module `11000339101`, so nothing else needs importing.

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

## 6. Expand

Body site value set (`<< 123037004`), all 45,367 codes in one call:

```sh
curl -u test:test -H 'Accept: application/fhir+json' -G 'http://localhost:8080/snowowl/fhir/ValueSet/$expand' \
  --data-urlencode 'url=http://snomed.info/sct/11000339101?fhir_vs=ecl/<<123037004' \
  --data-urlencode 'count=50000' -o body-site-vs.json
```

Other queries:

```sh
# native ECL, count only
curl -u test:test -G http://localhost:8080/snowowl/snomedct/SNOMEDCT-UZ/concepts \
  --data-urlencode 'ecl=<<123037004' --data-urlencode 'active=true' --data-urlencode 'limit=0'
# Uzbek display for a code
curl -u test:test -H 'Accept: application/fhir+json' \
  'http://localhost:8080/snowowl/fhir/CodeSystem/$lookup?system=http://snomed.info/sct&version=http://snomed.info/sct/11000339101&code=129173004&displayLanguage=uz'
```

Quirks:

- Use `fhir_vs=ecl/<<X`, not `fhir_vs=isa/X`: Snow Owl's `isa/` leaves out the root concept itself.
- Expansion `display` is the FSN ("... (body structure)"). For the preferred term add `includeDesignations=true` or use `$lookup`.
- The release has only 11 Uzbek descriptions on 10 concepts, none of them body structures, so `displayLanguage=uz` on body sites still returns English.
- Counts differ slightly from tx.fhir.org or Ontoserver, which run newer International releases (for example 45,994 on 20261001).

## Stop, start, reset

```sh
docker compose down      # stop, keeps the imported data
docker compose up -d     # start again, no re-import needed
docker compose down -v   # stop and delete all data
```
