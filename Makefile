.PHONY: check report

check:
	python3 -m py_compile app/cam_dashboard.py app/deploy_report.py
	python3 ops/check_page.py
	python3 ops/secret_scan.py
	python3 app/deploy_report.py

report:
	python3 app/deploy_report.py
