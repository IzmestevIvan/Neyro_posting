PYTHON ?= .venv/bin/python
export PYTHON_DOTENV_DISABLED = 1

.PHONY: test test-ui test-browser check hooks ansible-check ansible-apply k8s-render ops-status ops-backup
check:
	$(PYTHON) scripts/ci/repository_check.py
	git diff --check

test:
	@test -n "$(TEST_DATABASE_URL)" || (echo 'Set TEST_DATABASE_URL to the isolated localhost neyro_test database'; exit 1)
	$(PYTHON) -m pytest -q

test-ui:
	npm test

test-browser:
	npm run test:browser

hooks:
	git config core.hooksPath .githooks

ansible-check:
	ANSIBLE_CONFIG=infra/ansible/ansible.cfg ansible-playbook infra/ansible/site.yml --check --diff

ansible-apply:
	ANSIBLE_CONFIG=infra/ansible/ansible.cfg ansible-playbook infra/ansible/site.yml

k8s-render:
	kubectl kustomize infra/kubernetes

ops-status:
	ANSIBLE_CONFIG=infra/ansible/ansible.cfg ansible-playbook infra/ansible/operations.yml

ops-backup:
	ANSIBLE_CONFIG=infra/ansible/ansible.cfg ansible-playbook infra/ansible/operations.yml --limit reserve -e operation=backup
