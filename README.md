# 🤖 AI Web Scraper

An AI-powered web scraping tool built with **Streamlit**, **Selenium**, **BeautifulSoup**, and **Ollama (LangChain)**. Enter any URL, scrape its content, and use a local LLM to intelligently parse and extract exactly what you need.

---

## ✨ Features

- 🌐 Scrape any website using Selenium + ChromeDriver
- 🧹 Clean and extract body content automatically
- 🤖 Use a local AI model (Ollama) to parse content based on your description
- 💬 Simple Streamlit UI — no coding required to use
- 🔒 Runs entirely locally — no data sent to external APIs

---

## 🗂️ Project Structure

```
scraping/
└── AI_web_scraper/
    ├── main.py          # Streamlit UI
    ├── scrape.py        # Selenium scraper + content cleaner
    ├── parse.py         # Ollama LLM parser
    └── requirements.txt
```

---

## 🚀 Getting Started

### 1. Clone the repository

```bash
git clone https://github.com/fekih-ahmed/AI-web-scraping.git
cd AI-web-scraping
```

### 2. Install dependencies

```bash
pip install -r scraping/AI_web_scraper/requirements.txt
```

### 3. Install & run Ollama

Download Ollama from [ollama.com](https://ollama.com), then pull a model:

```bash
ollama pull llama3
```

### 4. Run the app

```bash
streamlit run scraping/AI_web_scraper/main.py
```

---

## 🛠️ Tech Stack

| Tool | Purpose |
|---|---|
| Streamlit | Web UI |
| Selenium | Browser automation / scraping |
| BeautifulSoup4 | HTML parsing & cleaning |
| LangChain + Ollama | Local LLM for intelligent parsing |
| Python-dotenv | Environment variable management |

---

## 📌 Notes

- Make sure `chromedriver.exe` matches your installed Chrome version
- Ollama must be running locally before starting the app (`ollama serve`)
