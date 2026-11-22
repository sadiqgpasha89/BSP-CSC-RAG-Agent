"""Message catalogue for system-authored customer messages (en / es / hi).

Only *frame* text is translated here (confirmations, prompts, handover and error notices). Facts (amounts, dates,
plan names, references) are inserted from system-of-record data and never translated or generated. Missing keys or
languages fall back to English and are counted so the evaluation suite can flag untranslated coverage.
"""

from typing import Dict

MESSAGES: Dict[str, Dict[str, str]] = {}
FALLBACKS_USED: Dict[str, int] = {}


def _add(key: str, en: str, es: str = "", hi: str = "") -> None:
    MESSAGES[key] = {"en": en}
    if es:
        MESSAGES[key]["es"] = es
    if hi:
        MESSAGES[key]["hi"] = hi


def render(key: str, lang: str = "en", **kw) -> str:
    entry = MESSAGES.get(key)
    if entry is None:
        FALLBACKS_USED[f"missing:{key}"] = FALLBACKS_USED.get(f"missing:{key}", 0) + 1
        return key
    text = entry.get(lang)
    if text is None:
        FALLBACKS_USED[f"{key}:{lang}"] = FALLBACKS_USED.get(f"{key}:{lang}", 0) + 1
        text = entry["en"]
    try:
        return text.format(**kw)
    except (KeyError, IndexError):
        return text


def coverage() -> Dict[str, float]:
    total = len(MESSAGES) or 1
    langs = {l for m in MESSAGES.values() for l in m}
    return {l: round(sum(1 for m in MESSAGES.values() if l in m) / total, 3) for l in sorted(langs)}


# --- transport / framing ---------------------------------------------------------------------------------------
_add("otp_message", "Your verification code is {code}. It expires in {minutes} minutes. We will never ask you to share it with anyone.",
     "Su código de verificación es {code}. Caduca en {minutes} minutos. Nunca le pediremos que lo comparta.",
     "आपका सत्यापन कोड {code} है। यह {minutes} मिनट में समाप्त हो जाएगा। इसे किसी के साथ साझा न करें।")
_add("confirm_prompt", "Please confirm: {summary}\nReply YES to confirm or NO to cancel. This request expires in {minutes} minutes.",
     "Confirme por favor: {summary}\nResponda SÍ para confirmar o NO para cancelar. Esta solicitud caduca en {minutes} minutos.",
     "कृपया पुष्टि करें: {summary}\nपुष्टि के लिए हाँ और रद्द करने के लिए नहीं लिखें। यह अनुरोध {minutes} मिनट में समाप्त हो जाएगा।")
_add("confirm_cancelled", "Okay, I've cancelled that. Nothing was changed.",
     "De acuerdo, lo he cancelado. No se realizó ningún cambio.", "ठीक है, मैंने इसे रद्द कर दिया। कुछ भी नहीं बदला गया।")
_add("confirm_expired", "That request has expired, so nothing was changed. Tell me again if you'd like to proceed.",
     "Esa solicitud caducó, por lo que no se cambió nada. Dígamelo de nuevo si desea continuar.",
     "वह अनुरोध समाप्त हो गया, इसलिए कुछ भी नहीं बदला। आगे बढ़ना हो तो मुझे फिर बताएं।")
_add("handover_notice", "I'm handing this over to a human colleague now. They will see our whole conversation, so you won't need to repeat anything. Reference: {ref}.",
     "Le paso este caso a un compañero humano. Verá toda nuestra conversación, así que no tendrá que repetir nada. Referencia: {ref}.",
     "मैं यह मामला अब एक मानव सहयोगी को सौंप रहा हूँ। वे हमारी पूरी बातचीत देख सकेंगे, इसलिए आपको कुछ दोहराना नहीं पड़ेगा। संदर्भ: {ref}।")
_add("handover_waiting", "A human colleague has your case ({ref}) and will reply here shortly. You don't need to repeat anything.",
     "Un compañero humano tiene su caso ({ref}) y responderá aquí en breve. No necesita repetir nada.",
     "एक मानव सहयोगी के पास आपका मामला ({ref}) है और वे जल्द ही यहीं उत्तर देंगे। आपको कुछ दोहराना नहीं है।")
_add("handover_joined", "{agent} from our support team has joined the conversation.",
     "{agent} de nuestro equipo de soporte se ha unido a la conversación.", "हमारी सहायता टीम से {agent} बातचीत में शामिल हो गए हैं।")
_add("greeting", "Hello{name_part}! I can help with your balance and usage, bills and payments, plans, connection problems, SIM/eSIM, orders and complaints. What do you need?",
     "¡Hola{name_part}! Puedo ayudarle con saldo y consumo, facturas y pagos, planes, problemas de conexión, SIM/eSIM, pedidos y quejas. ¿Qué necesita?",
     "नमस्ते{name_part}! मैं बैलेंस और उपयोग, बिल और भुगतान, प्लान, कनेक्शन समस्या, SIM/eSIM, ऑर्डर और शिकायतों में मदद कर सकता हूँ। आपको क्या चाहिए?")
_add("auth_required", "I need to verify who you are before I can share account details. Please sign in first.",
     "Necesito verificar su identidad antes de compartir datos de la cuenta. Inicie sesión primero.",
     "खाते की जानकारी साझा करने से पहले मुझे आपकी पहचान सत्यापित करनी होगी। कृपया पहले साइन इन करें।")
_add("stepup_required", "For your security I need to verify you with a one-time code before I can do this. I've sent a code to your registered contact. Please reply with the 6-digit code.",
     "Por su seguridad necesito verificarle con un código de un solo uso. Enviamos un código a su contacto registrado. Responda con el código de 6 dígitos.",
     "आपकी सुरक्षा के लिए मुझे वन-टाइम कोड से सत्यापन करना होगा। मैंने आपके पंजीकृत संपर्क पर कोड भेजा है। कृपया 6 अंकों का कोड भेजें।")
_add("stepup_ok", "Thank you, you're verified for the next {minutes} minutes.",
     "Gracias, está verificado durante los próximos {minutes} minutos.", "धन्यवाद, आप अगले {minutes} मिनट के लिए सत्यापित हैं।")
_add("stepup_bad", "That code wasn't right. You have {left} attempt(s) left.",
     "Ese código no es correcto. Le quedan {left} intento(s).", "वह कोड सही नहीं था। आपके पास {left} प्रयास शेष हैं।")
_add("stepup_locked", "Too many incorrect codes, so I've paused this for your security. A specialist will help you verify.",
     "Demasiados códigos incorrectos; lo he pausado por su seguridad. Un especialista le ayudará a verificarse.",
     "बहुत अधिक गलत कोड, इसलिए सुरक्षा के लिए मैंने इसे रोक दिया है। एक विशेषज्ञ सत्यापन में आपकी मदद करेगा।")
_add("system_unavailable", "I couldn't reach that system just now, so I can't confirm anything yet. I haven't changed anything. I'll pass this to a colleague so it isn't lost.",
     "No pude acceder a ese sistema ahora, así que no puedo confirmar nada todavía. No he cambiado nada. Lo paso a un compañero para que no se pierda.",
     "मैं अभी उस सिस्टम तक नहीं पहुँच सका, इसलिए अभी कुछ भी पुष्टि नहीं कर सकता। मैंने कुछ नहीं बदला। इसे खोने से बचाने के लिए मैं इसे सहयोगी को सौंप रहा हूँ।")
_add("anything_else", "Is there anything else I can help with?", "¿Puedo ayudarle con algo más?", "क्या मैं किसी और बात में मदद कर सकता हूँ?")
_add("not_understood", "I'm not sure I understood. You can say things like \"check my balance\", \"explain my bill\", \"recharge\", \"my internet is slow\" or \"talk to a person\".",
     "No estoy seguro de haber entendido. Puede decir \"consultar mi saldo\", \"explicar mi factura\", \"recargar\", \"mi internet va lento\" o \"hablar con una persona\".",
     "मुझे पूरी तरह समझ नहीं आया। आप \"बैलेंस देखें\", \"बिल समझाएं\", \"रिचार्ज\", \"इंटरनेट धीमा है\" या \"किसी व्यक्ति से बात\" जैसा कह सकते हैं।")
_add("workflow_cancelled", "No problem, I've stopped that. Nothing was changed.", "Sin problema, lo he detenido. No se cambió nada.", "कोई बात नहीं, मैंने इसे रोक दिया। कुछ भी नहीं बदला।")
_add("lang_notice", "I'll continue in {language}.", "Continuaré en {language}.", "मैं {language} में जारी रखूँगा।")
# --- notifications ---------------------------------------------------------------------------------------------
_add("notify_outage", "Service notice: there is a network issue affecting {sector}. Estimated restoration: {eta}. We'll message you when it's fixed.",
     "Aviso de servicio: hay un problema de red en {sector}. Restablecimiento estimado: {eta}. Le avisaremos cuando se resuelva.",
     "सेवा सूचना: {sector} में नेटवर्क समस्या है। अनुमानित बहाली: {eta}। ठीक होने पर हम आपको संदेश भेजेंगे।")
_add("notify_case_update", "Update on your case {case_id}: status is now {status}. {note}",
     "Actualización de su caso {case_id}: el estado es ahora {status}. {note}", "आपके केस {case_id} का अपडेट: स्थिति अब {status} है। {note}")
_add("notify_receipt", "Receipt: {summary}. Reference {ref}.", "Recibo: {summary}. Referencia {ref}.", "रसीद: {summary}। संदर्भ {ref}।")

_add("notify_restored", "Good news: the network issue affecting {sector} has been resolved. If anything is still not working, reply here and we'll help.",
     "Buenas noticias: el problema de red en {sector} se ha resuelto. Si algo sigue sin funcionar, responda aquí y le ayudaremos.",
     "अच्छी खबर: {sector} में नेटवर्क समस्या ठीक हो गई है। अगर अभी भी कुछ काम नहीं कर रहा, तो यहाँ जवाब दें, हम मदद करेंगे।")
