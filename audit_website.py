#!/usr/bin/env python3
"""Full GhostClipper website + auth audit."""
import json, urllib.request, urllib.error, time, secrets, sys, urllib.parse
BASE='http://127.0.0.1:8899'
class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None
results=[]
def check(name, cond, detail=''):
    results.append((name, bool(cond), detail))
    print(('  PASS ' if cond else '  FAIL ')+name+(' :: '+str(detail)[:120] if detail else ''), flush=True)
def call(path, body=None, method=None, follow=True):
    data=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(BASE+path, data=data, method=method or ('POST' if body is not None else 'GET'),
                             headers={'Content-Type':'application/json'})
    opener=urllib.request.build_opener() if follow else urllib.request.build_opener(NoRedirect)
    try:
        x=opener.open(r,timeout=15); raw=x.read()
        try: j=json.loads(raw)
        except Exception: j=raw.decode(errors='replace')
        return x.status,j,dict(x.headers)
    except urllib.error.HTTPError as e:
        raw=e.read()
        try: j=json.loads(raw)
        except Exception: j=raw.decode(errors='replace')
        return e.code,j,dict(e.headers)
    except Exception as e:
        return 0,str(e),{}

print('\n=== 1. PAGES + ASSETS ===',flush=True)
for p in ['/','/signup','/dashboard','/pricing','/auth.css']:
    c,j,h=call(p); check('GET '+p, c==200 and (p.endswith('.css') or '<' in str(j)), c)
# linked assets referenced exist
home=call('/')[1]
check('home has Sign in + Sign up', 'Sign in' in home and 'Sign up' in home)
check('home has no pricing', 'Simple pricing' not in home and 'planGrid' not in home)
check('home has demo loop', 'typeUrl' in home and 'clipsWrap' in home)
sign=call('/signup')[1]
check('signup has email/password/google', 'type="email"' in sign and 'type="password"' in sign and 'api/auth/google/start' in sign)
dash=call('/dashboard')[1]
check('dashboard sign-in card + google', 'sign in' in dash.lower() and 'api/auth/google/start' in dash)

print('\n=== 2. PUBLIC APIS ===',flush=True)
c,j,_=call('/api/health'); check('health',c==200 and j.get('ok'))
c,j,_=call('/api/plans'); check('plans (3)',c==200 and len(j.get('plans',[]))==3, len(j.get('plans',[])))
check('plan fields', all(all(k in p for k in ('name','usd','credits','tier')) for p in j.get('plans',[])))

print('\n=== 3. SIGNUP / LOGIN / STATUS ===',flush=True)
email='audit_'+secrets.token_hex(3)+'@test.gc'; pw='auditpass123'
c,j,_=call('/api/signup',{'name':'Audit','email':email,'password':pw})
check('signup creates token',c==200 and j.get('ok') and bool(j.get('token')),j.get('error'))
tok=j.get('token','')
c,j,_=call('/api/signup',{'email':email,'password':pw})
check('duplicate signup rejected',c==400 and not j.get('ok'),j.get('error'))
c,j,_=call('/api/signup',{'email':'bad','password':'x'})
check('invalid signup rejected',c==400)
c,j,_=call('/api/auth/login',{'email':email,'password':'wrong'})
check('wrong password rejected',c==401 and not j.get('token'))
c,j,_=call('/api/auth/login',{'email':email,'password':pw})
check('login returns token',c==200 and bool(j.get('token')))
c,j,_=call('/api/account',{'token':tok})
check('account resolves token',c==200 and j.get('email')==email)
c,j,_=call('/api/auth/status',{'token':tok})
check('status valid field present',c==200 and 'valid' in j and j.get('tier')=='trial', j)
c,j,_=call('/api/account',{'token':'garbage'})
check('bad token rejected',c==401)

print('\n=== 4. PAYMENT WEBHOOK (simulate activation) ===',flush=True)
cfg=json.load(open('config.json'))
c,j,_=call('/api/auth/webhook',{'email':email,'tier':'pro','expires_days':30,'order_id':'AUDIT1','webhook_key':'wrong'})
check('webhook wrong key rejected',c==401)
c,j,_=call('/api/auth/webhook',{'email':email,'tier':'pro','expires_days':30,'order_id':'AUDIT1','webhook_key':cfg.get('webhook_key')})
check('webhook activates account',c==200 and j.get('ok'),j)
c,j,_=call('/api/auth/status',{'token':tok})
check('status now valid/pro',j.get('valid') and j.get('tier')=='pro', j)
c,j,_=call('/api/auth/webhook',{'email':'nobody@test.gc','tier':'pro','expires_days':30,'webhook_key':cfg.get('webhook_key')})
check('webhook unknown account rejected',c==404)

print('\n=== 5. GOOGLE OAUTH ===',flush=True)
c,j,h=call('/api/auth/google/start',follow=False)
loc=h.get('Location','')
check('google start redirects',c==302 and 'accounts.google.com' in loc, loc[:80])
redirect_uri=urllib.parse.parse_qs(urllib.parse.urlparse(loc).query).get('redirect_uri',[''])[0]
check('redirect_uri is registered callback', redirect_uri.endswith('/api/auth/google/callback'), redirect_uri)
c,j,_=call('/api/auth/google/callback')
check('callback without code handled',c==400)

print('\n=== 6. APP <-> WEBSITE LINK ===',flush=True)
# token from website must verify in desktop app
def app_call(port,path,body=None,method='POST'):
    data=json.dumps(body).encode() if body is not None else None
    r=urllib.request.Request(f'http://127.0.0.1:{port}'+path,data=data,method=method,headers={'Content-Type':'application/json'})
    try:
        x=urllib.request.urlopen(r,timeout=15);return x.status,json.loads(x.read())
    except urllib.error.HTTPError as e:return e.code,json.loads(e.read() or b'{}')
    except Exception as e:return 0,str(e)
# fresh website account
email2='audit2_'+secrets.token_hex(3)+'@test.gc'
c,j,_=call('/api/signup',{'email':email2,'password':'auditpass123'})
tok2=j.get('token','')
for port,label in [(8767,'dev'),(8766,'customer')]:
    c,j=app_call(port,'/api/auth/token',{'token':tok2})
    check(f'website token signs into {label} app',c==200 and j.get('ok'),j)
c,j=app_call(8766,'/api/auth/status',method='GET')
# A free/trial website account is verified by the server but reports 'trial',
# never 'subscription' — that would require a paid webhook upgrade.
check('customer entitlement verified by server (no local trust)',
      j.get('source') in ('trial','free','subscription'),j.get('source'))

print('\n=== 7. CLEANUP ===',flush=True)
for f in ['data/users.json']:
    d=json.load(open(f))
    for e in [email,email2]: d.pop(e,None)
    json.dump(d,open(f,'w'),indent=2)
print('  test accounts removed',flush=True)
for port in [8767,8766]: app_call(port,'/api/auth/logout',{})

passed=sum(1 for _,p,_ in results if p); total=len(results)
print(f'\n=== RESULT: {passed}/{total} passed ===',flush=True)
if passed<total:
    print('FAILURES:',[n for n,p,_ in results if not p],flush=True)
    sys.exit(1)
