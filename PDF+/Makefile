run:
	python3 manage.py runserver
PORT ?= 8000
stop:
	@pids=$$(lsof -ti tcp:$(PORT) -sTCP:LISTEN); \
	if [ -n "$$pids" ]; then kill $$pids && echo "Зупинено процес(и) на порту $(PORT): $$pids"; \
	else echo "Порт $(PORT) вільний"; fi
