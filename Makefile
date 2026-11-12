.PHONY: check report

check:
	python3 -m py_compile app/cam_dashboard.py app/deploy_report.py
	python3 -c 'from pathlib import Path; v=Path("VERSION").read_text().strip(); html=Path("app/index.html").read_text(); py=Path("app/cam_dashboard.py").read_text(); assert f"content=\"{v}\"" in html; assert f"APP_VERSION = \"{v}\"" in py; print("VERSION", v)'
	python3 ops/check_page.py
	python3 ops/secret_scan.py
	python3 deploy/setup.py --self-test
	python3 app/deploy_report.py

report:
	python3 app/deploy_report.py
