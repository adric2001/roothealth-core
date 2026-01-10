FROM python:3.10-slim

WORKDIR /app

ENV HOME=/root

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install streamlit pandas plotly watchdog pycognito

RUN mkdir -p /root/.streamlit
RUN printf '[server]\n\
headless = true\n\
enableCORS = false\n\
enableXsrfProtection = false\n\
enableWebsocketCompression = false\n\
address = "0.0.0.0"\n\
port = 8080\n\
\n\
[browser]\n\
gatherUsageStats = false\n\
' > /root/.streamlit/config.toml

COPY . .

EXPOSE 8080

CMD ["streamlit", "run", "dashboard.py"]