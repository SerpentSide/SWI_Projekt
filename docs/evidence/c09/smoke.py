import json, sqlite3, sys, urllib.request, datetime
B="http://localhost:8077"; DB=sys.argv[1]
def call(m, p, body=None, h={}):
    r=urllib.request.Request(B+p, method=m, data=json.dumps(body).encode() if body is not None else None,
        headers={"content-type":"application/json", **h})
    try:
        with urllib.request.urlopen(r) as x: return x.status, json.load(x)
    except urllib.error.HTTPError as e: return e.code, json.load(e)
c=sqlite3.connect(DB)
vip={r[0] for r in c.execute("select id from seats where requires_approval=1")}
uid=call("POST","/auth/login",{"email":"demo@example.com"})[1]["user_id"]
op={"X-User-Id":str(call("POST","/auth/login",{"email":"boxoffice@example.com"})[1]["user_id"])}
now=datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
for sc in call("GET","/screenings")[1]:
    if sc["starts_at"]<=now: continue
    seats=[s for s in call("GET",f"/screenings/{sc['id']}/availability")[1]["seats"] if s["id"] in vip and not s["occupied"]]
    for i in range(len(seats)-1):
        st,b=call("POST","/reservations",{"user_id":uid,"screening_id":sc["id"],"seat_ids":[seats[i]["id"],seats[i+1]["id"]]})
        if st==201: break
    else: continue
    break
rid=b["reservation_id"]; print("create", st, b)
print("confirm", call("POST",f"/reservations/{rid}/confirm"))
print("db row", c.execute("select state, pending_until from reservations where id=?",(rid,)).fetchone(), "server now ~", now)
# simulate 30 min passing by moving the stored deadline to the past (direct SQL in the smoke DB only)
c.execute("update reservations set pending_until=? where id=?",(now,rid)); c.commit()
print("GET after deadline", call("GET",f"/reservations/{rid}")[1]["state"])
print("approve after deadline", call("POST",f"/reservations/{rid}/decision",{"decision":"approve"},op))
print("db row after", c.execute("select state, confirmed_at from reservations where id=?",(rid,)).fetchone())
