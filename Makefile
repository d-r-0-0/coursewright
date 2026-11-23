# cantrips/verify.py is the single definition of the gate; its stages
# live in verify.toml.  These targets are thin aliases.
# No `make` on Windows: run `python cantrips/verify.py` there.

.PHONY: verify lint test

verify:
	python cantrips/verify.py

lint:
	python cantrips/verify.py --only lint

test:
	python cantrips/verify.py --only test
