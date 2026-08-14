# django-orders

A Django + Celery + Postgres order-management service, written as a **fixture**
for `05` S1.4. It is not a runnable application and is not meant to be one: it is
a corpus of declarations, sized to the sprint's floors (>=30 routes, >=40 tables,
>=8 jobs, >=60 config keys) so that the web pack's extractors have a realistic
subject and `fixtures/labeled/django-orders.identities.json` has something to be
the ground truth *of*.

**Nothing here is imported or executed.** Every extractor in the pack reads this
tree as text through a grammar -- `02` §7 obligation 1 -- so the code need only be
syntactically valid Python, not importable Django.
