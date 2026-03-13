# StorePilot - Handover per nuovo Mac / nuovo Codex

Data handover: 2026-02-28 (Europe/Rome)

## 1) Stato progetto
- Repo GitHub: https://github.com/andrearuggiero83/StorePilot
- Branch principale: `main`
- Stato git locale al momento dell'ultimo check: pulito e allineato (`main...origin/main`)

## 2) Modifiche principali implementate

### Report / Lead generation
- Sezione REPORT evoluta con raccolta lead:
  - email
  - localita progetto
  - consenso privacy (obbligatorio)
  - consenso marketing (facoltativo)
- Pulsanti invio presenti:
  - `Invia report PDF`
  - `Invia report Excel`
- Download locale mantenuto come opzione secondaria.

### Privacy Policy
- Creata pagina multipage: `pages/Privacy_Policy.py`
- Aggiunta route interna fallback via query param in `app.py`: `?view=privacy`
- Link privacy in REPORT:
  - usa `st.secrets["privacy_policy_url"]` se presente
  - altrimenti usa route interna
- Testo privacy aggiornato con versione fornita dal cliente (IT completa + EN sintetica).

### Google Sheets (lead storage)
- Integrato salvataggio lead su Google Sheet `StorePilot_Leads`, tab `Leads`.
- Logica robusta su header dinamico:
  - legge riga 1 (`row_values(1)`)
  - costruisce `row_data`
  - genera `row_values = [row_data.get(col, "") for col in header]`
  - scrive con `append_row(..., value_input_option="USER_ENTERED")`
- Colonne consenso ora valorizzate esplicitamente:
  - `consenso_privacy` -> `"true"/"false"`
  - `consenso_marketing` -> `"true"/"false"`

## 3) File toccati
- `app.py`
- `pages/Privacy_Policy.py`
- `requirements.txt`
- `.gitignore`
- `README.md`

## 4) Config locale richiesta

### Python env
```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Secrets locali (NON in git)
File: `.streamlit/secrets.toml`

Sezione usata per Google Service Account:
```toml
[gcp_service_account]
type = "service_account"
project_id = "..."
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "..."
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

Secrets opzionali usati nel codice:
- `privacy_policy_url`
- `privacy_contact_email`
- `mailersend_api_key` (placeholder integrazione)
- `internal_report_copy_email` (placeholder integrazione)

## 5) Sicurezza
- `.gitignore` include:
  - `__pycache__/`
  - `.env`
  - `.streamlit/secrets.toml`
- Audit rapido anti-secrets fatto su repo tracciato: nessun secret rilevato nei file versionati.
- Un PAT GitHub era stato accidentalmente condiviso in chat ed e stato revocato.

## 6) Dipendenze rilevanti aggiunte
- `gspread`
- `google-auth`

## 7) Note operative
- In locale c'e stata una warning Streamlit su `selected_dayparts_widget` risolta rimuovendo `default=` dal multiselect quando viene usato `key` + session state.
- Il salvataggio lead e integrato senza modificare calcoli economici/UI core.

## 8) TODO consigliati (prossimo step)
1. Verifica end-to-end scrittura lead su `StorePilot_Leads` con un invio reale dal form.
2. Integrare invio email reale via MailerSend (attualmente funzione placeholder).
3. Aggiungere logging applicativo minimo non sensibile per osservabilita errori (`sheet_error`, `email_error`).
4. Eventuale hardening mapping colonne con normalizzazione case-insensitive header.

## 9) Avvio app
```bash
streamlit run app.py
```
