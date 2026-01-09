# Use a lightweight Python Linux image
FROM python:3.9-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install streamlit pandas plotly

# --- CONFIGURATION (Force Port 8080) ---
RUN mkdir -p /root/.streamlit
RUN bash -c 'echo -e "\
[server]\n\
headless = true\n\
enableCORS = false\n\
enableXsrfProtection = false\n\
enableWebsocketCompression = false\n\
address = \"0.0.0.0\"\n\
port = 8080\n\
\n\
[browser]\n\
gatherUsageStats = false\n\
" > /root/.streamlit/config.toml'

# Copy the rest of the code
COPY . .

# Expose the standard AWS port
EXPOSE 8080

# Run the app
CMD ["streamlit", "run", "dashboard.py"]