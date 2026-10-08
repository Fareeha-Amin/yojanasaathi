# Voice + phone line (Aryan)

Pipecat pipeline: Sarvam Saaras STT -> custom processor that POSTs the transcript to
`/turn/{case_id}` -> Sarvam Bulbul TTS. Phone line: Twilio number -> Pipecat
telephony transport -> same pipeline, case_id from the caller's number.
