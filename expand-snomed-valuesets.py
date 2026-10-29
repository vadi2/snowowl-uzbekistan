#!/usr/bin/env python3
"""Expand the SNOMED CT value sets of FHIR IG packages against a local Snow Owl."""
# Snow Owl OSS cannot store FHIR ValueSets, so composes are evaluated here: is-a filters via implicit isa value sets, concepts via $lookup
import argparse, base64, datetime, io, json, os, sys, tarfile, urllib.error, urllib.parse, urllib.request

SCT = 'http://snomed.info/sct'
EN_US = '900000000000509007'
UZ = '21000339107'
PREFERRED = '900000000000548007'


def get(url, auth, accept='application/fhir+json'):
    req = urllib.request.Request(url, headers={'Accept': accept})
    if auth:
        req.add_header('Authorization', 'Basic ' + base64.b64encode(auth.encode()).decode())
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def package_valuesets(spec, registry):
    name, _, version = spec.partition('#')
    if not version:
        version = json.loads(get(f'{registry}/{name}', None, 'application/json'))['dist-tags']['latest']
    tgz = get(f'{registry}/{name}/{version}', None, 'application/gzip')
    with tarfile.open(fileobj=io.BytesIO(tgz)) as tar:
        for m in tar.getmembers():
            base = os.path.basename(m.name)
            if m.isfile() and m.name.startswith('package/') and base.startswith('ValueSet-') and base.endswith('.json'):
                yield f'{name}#{version}', json.load(tar.extractfile(m))


def preferred_terms(designations):
    terms = {}
    for d in designations or []:
        for ctx in d.get('extension', []):
            parts = {e['url']: e['valueCoding']['code'] for e in ctx.get('extension', [])}
            if parts.get('role') == PREFERRED and parts.get('type') == '900000000000013009':
                terms.setdefault(parts.get('context'), d['value'])
    return terms


class SnowOwl:
    def __init__(self, base, edition, auth):
        self.base, self.edition, self.auth = base.rstrip('/'), edition, auth

    def is_a(self, code):
        vs_url = f'{self.edition}?fhir_vs=isa/{code}'
        url = (f'{self.base}/ValueSet/$expand?url={urllib.parse.quote(vs_url, safe="")}'
               f'&count=10000&includeDesignations=true')
        out = {}
        while url:
            exp = json.loads(get(url, self.auth))['expansion']
            for c in exp.get('contains', []):
                t = preferred_terms(c.get('designation'))
                out[c['code']] = (t.get(EN_US) or c['display'], t.get(UZ))
            url = exp.get('next') if exp.get('contains') else None
        # FHIR is-a includes the concept itself; Snow Owl's isa/ leaves it out
        root = self.lookup(code)
        if root and not root[2]:
            out[code] = root[:2]
        return out

    def lookup(self, code):
        q = f'{self.base}/CodeSystem/$lookup?system={SCT}&version={self.edition}&code={code}&property=inactive'
        try:
            en = json.loads(get(q, self.auth))
            uz = json.loads(get(q + '&displayLanguage=uz', self.auth))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        display = lambda p: next(x['valueString'] for x in p['parameter'] if x['name'] == 'display')
        inactive = any(x['name'] == 'property' and any(y.get('valueBoolean') for y in x['part'] if y['name'] == 'value')
                       for x in en['parameter'])
        uz_term = display(uz)
        return display(en), (uz_term if uz_term != code else None), inactive


def evaluate(server, clause, problems):
    codes = {}
    if clause.get('concept'):
        for c in clause['concept']:
            r = server.lookup(c['code'])
            if r is None:
                problems.append(f"{c['code']} unknown")
            elif r[2]:
                problems.append(f"{c['code']} inactive")
            else:
                codes[c['code']] = r[:2]
    elif clause.get('filter'):
        for i, f in enumerate(clause['filter']):
            if f.get('property') != 'concept' or f.get('op') != 'is-a':
                raise ValueError(f"unsupported filter {f.get('property')} {f.get('op')}")
            found = server.is_a(f['value'])
            codes = found if i == 0 else {k: v for k, v in codes.items() if k in found}
    else:
        raise ValueError('includes all of SNOMED CT')
    return codes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('packages', nargs='*', default=['uz.dhp.core', 'uz.dhp.integrations'],
                    help='package names, optionally name#version (default: latest)')
    ap.add_argument('--server', default='http://localhost:8080/snowowl/fhir')
    ap.add_argument('--edition', default=f'{SCT}/11000339101')
    ap.add_argument('--auth', default='test:test')
    ap.add_argument('--registry', default='https://packages.fhir.org')
    ap.add_argument('--out', default='expansions')
    args = ap.parse_args()
    server = SnowOwl(args.server, args.edition, args.auth)

    for spec in args.packages:
        for pkg, vs in package_valuesets(spec, args.registry):
            compose = vs.get('compose', {})
            clauses = compose.get('include', []) + compose.get('exclude', [])
            if not clauses or any(c.get('system') != SCT or c.get('valueSet') for c in clauses):
                continue
            problems = []
            try:
                codes = {}
                for inc in compose.get('include', []):
                    codes.update(evaluate(server, inc, problems))
                for exc in compose.get('exclude', []):
                    for k in evaluate(server, exc, []):
                        codes.pop(k, None)
            except ValueError as e:
                print(f'{pkg} {vs["id"]}: skipped, {e}')
                continue
            vs.pop('text', None)
            vs['expansion'] = {
                'timestamp': datetime.datetime.now().astimezone().isoformat(timespec='seconds'),
                'total': len(codes),
                'parameter': [{'name': 'used-codesystem', 'valueUri': f'{SCT}|{args.edition}'}],
                'contains': [dict({'system': SCT, 'code': k, 'display': en},
                                  **({'designation': [{'language': 'uz', 'value': uz}]} if uz else {}))
                             for k, (en, uz) in sorted(codes.items(), key=lambda i: int(i[0]))],
            }
            folder = os.path.join(args.out, pkg.split('#')[0])
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, f'ValueSet-{vs["id"]}.json'), 'w') as f:
                json.dump(vs, f, ensure_ascii=False, indent=1)
            note = f" ({', '.join(problems)})" if problems else ''
            print(f'{pkg} {vs["id"]}: {len(codes)}{note}')


if __name__ == '__main__':
    sys.exit(main())
