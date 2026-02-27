import streamlit as st


def _secret_get(key: str, default: str = "") -> str:
    try:
        return str(st.secrets.get(key, default) or default)
    except Exception:
        return str(default)


st.set_page_config(page_title="StorePilot - Privacy Policy", page_icon="🔒", layout="centered")

lang = st.session_state.get("lang", "IT")

if lang == "IT":
    st.title("Privacy Policy - StorePilot")
    st.caption("Ultimo aggiornamento: 23 febbraio 2026")

    st.markdown(
        """
**1. Titolare del trattamento**  
Il titolare del trattamento dei dati personali raccolti attraverso questo strumento e StorePilot, progetto digitale operante nel settore food retail.  
Per qualsiasi richiesta inerente alla privacy e possibile scrivere all'indirizzo: privacy@storepilot.eu

**2. Tipologie di dati trattati**  
I dati personali trattati attraverso StorePilot comprendono:
- Email fornita dall'utente
- Localita del progetto inserita dall'utente
- Parametri di simulazione inseriti dall'utente nel tool
- KPI generati dal simulatore (es. ricavi, EBITDA, break-even)
- Timestamp di invio richiesta
- Origine lead (es. "StorePilot - Horeca Consulting" o altra fonte)
- Versione del tool utilizzata
- Flag di consenso (privacy e, se esplicitato, marketing)

**3. Finalita del trattamento**  
I dati personali sono trattati per le seguenti finalita:  
a) Invio del report richiesto dall'utente tramite il simulatore;  
b) Invio di comunicazioni informative e commerciali relative ai servizi StorePilot e ad attivita di consulenza correlate, soltanto se l'utente ha prestato consenso esplicito a tale finalita.

**4. Base giuridica del trattamento**  
- Per l'invio del report richiesto: esecuzione di una richiesta dell'utente, ai sensi dell'art. 6(1)(b) del GDPR.  
- Per l'invio di comunicazioni commerciali: consenso espresso dall'interessato, ai sensi dell'art. 6(1)(a) del GDPR.

**5. Modalita di trattamento e sicurezza**  
I dati sono trattati con strumenti digitali e automatizzati, adottando misure tecniche e organizzative adeguate per garantire sicurezza, riservatezza e integrita, incluso:
- accesso limitato e controllato ai dati;
- uso di credenziali e chiavi sicure lato server;
- protezione delle comunicazioni;
- segregazione dei dati in sistemi e servizi protetti.
I dati non sono sottoposti a processi di decisione automatizzata o profilazione.

**6. Periodo di conservazione**  
- Lead senza consenso marketing: conservati per un massimo di 12 mesi dalla data di raccolta.  
- Lead con consenso marketing: conservati per un massimo di 24 mesi o fino a revoca del consenso.

**7. Destinatari dei dati / responsabili esterni**  
I dati possono essere comunicati a soggetti o servizi terzi per le finalita indicate, in qualita di responsabili del trattamento:
- Streamlit Cloud (hosting e operativita applicativa)
- MailerSend (servizio di invio email)
- Google Sheets o servizi analoghi (ove attivati per salvataggio dati)
- Fornitori di servizi tecnici necessari all'erogazione dell'applicazione

**8. Trasferimenti di dati verso paesi terzi**  
I dati possono essere trasferiti verso paesi al di fuori dello Spazio Economico Europeo (ad esempio nel caso di utilizzo di servizi cloud internazionali). Tali trasferimenti avvengono nel rispetto delle norme GDPR e con adeguate garanzie (come Standard Contractual Clauses ove applicabili).

**9. Diritti dell'interessato**  
Gli interessati possono in qualsiasi momento esercitare i diritti garantiti dagli articoli 15-22 del GDPR, tra cui:
- accesso ai propri dati personali;
- rettifica o aggiornamento dei dati;
- cancellazione ("diritto all'oblio");
- limitazione del trattamento;
- opposizione al trattamento;
- portabilita dei dati.
Le richieste possono essere inviate a privacy@storepilot.eu. L'interessato ha inoltre il diritto di proporre reclamo a un'autorita di controllo.

**10. Minori**  
Il servizio non e pensato ne rivolto a persone di eta inferiore ai 16 anni. Qualora un minore fornisse dati personali, si invita un genitore/tutore a contattare il titolare per richiederne la cancellazione.

**11. Cookie e strumenti di tracciamento**  
Il tool puo utilizzare cookie tecnici e funzionali necessari al corretto funzionamento della piattaforma (in particolare per Streamlit Cloud). Non vengono utilizzati strumenti di tracciamento pubblicitario o profilazione per finalita di marketing, ne pixel di terze parti per advertising.

**12. Modifiche alla privacy policy**  
La presente informativa puo essere soggetta ad aggiornamenti. La data dell'ultimo aggiornamento e indicata in testa alla pagina.
"""
    )
else:
    st.title("Privacy Policy - StorePilot")
    st.caption("Last update: February 23, 2026")

    st.markdown(
        f"""
**Controller**  
Data controller: **StorePilot**.

**Data processed**  
Email, project location, simulation inputs, generated KPIs, timestamp, lead source (StorePilot - Horeca Consulting), tool version.

**Purposes and legal basis**  
1. Report delivery requested by the user (performance of a user request - GDPR Art. 6(1)(b)).  
2. Optional follow-up contact (consent - GDPR Art. 6(1)(a)).

**Retention**  
- Non-converted leads: up to 12 months.  
- Leads with marketing consent: up to 24 months or until consent withdrawal.

**Processors / recipients (if enabled)**  
Streamlit Cloud (hosting), MailerSend (email delivery), Google Sheets/Drive (data storage).

**International transfers**  
Where extra-EEA transfers occur, appropriate GDPR safeguards are applied (including SCC where applicable).

**Your rights**  
Access, rectification, erasure, restriction, objection, portability, and complaint to a supervisory authority.

**Privacy contact**  
**privacy@storepilot.eu**

**Minors and cookies**  
StorePilot is not intended for users under 16.  
Technical cookies may be used for Streamlit platform operation; no marketing tracking is used in this feature unless specifically disclosed.
"""
    )
