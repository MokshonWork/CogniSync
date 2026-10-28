use anyhow::{anyhow, Context, Result};
use futures_util::StreamExt;
use reqwest::{Client, StatusCode};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::sync::mpsc::UnboundedSender;

const JSON_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(90);
/// Max silence between SSE chunks before we treat the stream as stalled.
const STREAM_IDLE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(120);

#[derive(Clone)]
pub struct Api {
    client: Client,
    base: String,
    token: String,
}
#[derive(Debug, Clone, Deserialize, serde::Serialize)]
pub struct LoginRequest {
    pub username: String,
    pub password: String,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize)]
pub struct AuthResponse {
    pub access_token: String,
    pub expires_in: u64,
    pub user_id: String,
    pub username: String,
}

#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize)]
pub struct Health {
    pub status: String,
    pub service: String,
    pub providers: Value,
    pub council_models: Vec<String>,
    pub chairman_model: String,
}
#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize)]
pub struct Session {
    pub id: String,
    pub title: String,
    pub created_at: String,
    #[serde(default)]
    pub updated_at: String,
    #[serde(default)]
    pub message_count: usize,
    #[serde(default)]
    pub messages: Vec<Value>,
}
#[allow(dead_code)]
#[derive(Debug, Clone, Deserialize)]
pub struct ModelCatalog {
    pub configured: bool,
    #[serde(default)]
    pub free_testing: bool,
    pub chairman_model: String,
    #[serde(default = "default_min_selection")]
    pub min_selection: usize,
    #[serde(default = "default_max_selection")]
    pub max_selection: usize,
    #[serde(default)]
    pub models: Vec<Model>,
}
fn default_min_selection() -> usize {
    1
}
fn default_max_selection() -> usize {
    4
}
#[derive(Debug, Clone, Deserialize)]
pub struct Model {
    pub id: String,
    pub name: String,
    #[serde(default)]
    pub free: bool,
}
#[derive(Debug, Clone, Deserialize)]
pub struct Tools {
    pub authentication: String,
    pub integrations: Value,
}

/// Body of `POST /api/conversations/{id}/message/events`.
/// Auth travels only in the `Authorization` header — never in the JSON body.
#[derive(Debug, Serialize)]
pub(crate) struct MessageRequest {
    pub content: String,
    pub models: Vec<String>,
    pub web_search: bool,
}

#[derive(Debug)]
pub enum ApiEvent {
    Health(Result<Health, String>),
    History(Result<Vec<Session>, String>),
    Open(Result<Session, String>),
    Created(Result<Session, String>),
    Models(Result<ModelCatalog, String>),
    Tools(Result<Tools, String>),
    Stream {
        session: String,
        event: Value,
    },
    /// The SSE body ended cleanly. The UI uses this to settle `loading` even
    /// when the backend never sent a terminal `done` event.
    StreamEnded {
        session: String,
        malformed: usize,
    },
    Login(Result<AuthResponse, String>),
    Error(String),
}

impl Api {
    pub fn new(base: String) -> Result<Self> {
        Ok(Self {
            client: Client::builder()
                .connect_timeout(std::time::Duration::from_secs(10))
                .build()?,
            base,
            token: String::new(),
        })
    }
    /// Store (or clear) the bearer token used on every protected request.
    pub fn set_token(&mut self, token: String) {
        self.token = token;
    }
    async fn json<T: for<'de> Deserialize<'de>>(&self, req: reqwest::RequestBuilder) -> Result<T> {
        let req = if self.token.is_empty() {
            req
        } else {
            req.bearer_auth(&self.token)
        };
        let response = req
            .timeout(JSON_TIMEOUT)
            .send()
            .await
            .map_err(describe_transport_error)?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(status_error(status, &body));
        }
        response.json().await.context("decoding backend response")
    }
    pub async fn health(&self) -> Result<Health> {
        self.json(self.client.get(format!("{}/", self.base))).await
    }
    pub async fn history(&self) -> Result<Vec<Session>> {
        self.json(self.client.get(format!("{}/api/conversations", self.base)))
            .await
    }
    pub async fn create(&self) -> Result<Session> {
        self.json(
            self.client
                .post(format!("{}/api/conversations", self.base))
                .json(&Value::Object(Default::default())),
        )
        .await
    }
    pub async fn open(&self, id: &str) -> Result<Session> {
        self.json(
            self.client
                .get(format!("{}/api/conversations/{id}", self.base)),
        )
        .await
    }
    pub async fn delete_conversation(&self, id: &str) -> Result<()> {
        if self.token.is_empty() {
            anyhow::bail!("not signed in");
        }
        let response = self
            .client
            .delete(format!("{}/api/conversations/{id}", self.base))
            .bearer_auth(&self.token)
            .timeout(JSON_TIMEOUT)
            .send()
            .await
            .map_err(describe_transport_error)?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(status_error(status, &body));
        }
        Ok(())
    }
    pub async fn models(&self) -> Result<ModelCatalog> {
        self.json(self.client.get(format!("{}/api/models", self.base)))
            .await
    }
    pub async fn login(&self, username: String, password: String) -> Result<AuthResponse> {
        self.json(
            self.client
                .post(format!("{}/auth/login", self.base))
                .json(&LoginRequest {
                    username: username.clone(),
                    password,
                }),
        )
        .await
    }
    pub async fn register(&self, username: String, password: String) -> Result<AuthResponse> {
        self.json(
            self.client
                .post(format!("{}/auth/register", self.base))
                .json(&LoginRequest { username, password }),
        )
        .await
    }

    pub async fn tools(&self) -> Result<Tools> {
        self.json(self.client.get(format!("{}/api/tools/status", self.base)))
            .await
    }

    /// Stream a council run as Server-Sent Events.
    ///
    /// No whole-request timeout is set: a council run legitimately takes
    /// longer than the JSON timeout. A per-chunk idle timeout still catches
    /// half-open connections. `StreamEnded` is always emitted after a clean
    /// EOF so a missing terminal `done` cannot leave the UI stuck loading.
    pub async fn stream_message(
        &self,
        id: String,
        content: String,
        models: Vec<String>,
        web_search: bool,
        tx: UnboundedSender<ApiEvent>,
    ) -> Result<()> {
        if self.token.is_empty() {
            anyhow::bail!("not signed in — run /login first");
        }
        let response = self
            .client
            .post(format!(
                "{}/api/conversations/{id}/message/events",
                self.base
            ))
            .header("Accept", "text/event-stream")
            .bearer_auth(&self.token)
            .json(&MessageRequest {
                content,
                models,
                web_search,
            })
            .send()
            .await
            .map_err(describe_transport_error)?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().await.unwrap_or_default();
            return Err(status_error(status, &body));
        }
        let mut stream = response.bytes_stream();
        let mut parser = SseParser::default();
        let mut malformed = 0usize;
        loop {
            let chunk = match tokio::time::timeout(STREAM_IDLE_TIMEOUT, stream.next()).await {
                Err(_) => {
                    return Err(anyhow!(
                        "stream stalled: no data for {}s (backend or proxy may have dropped the connection)",
                        STREAM_IDLE_TIMEOUT.as_secs()
                    ))
                }
                Ok(None) => break,
                Ok(Some(Err(e))) => return Err(anyhow!("connection lost during council run: {e}")),
                Ok(Some(Ok(chunk))) => chunk,
            };
            for data in parser.feed(&chunk) {
                let Some(event) = dispatch(&data, &id, &tx, &mut malformed) else {
                    continue;
                };
                if event.get("type").and_then(Value::as_str) == Some("done") {
                    return Ok(());
                }
            }
        }
        for data in parser.finish() {
            let Some(event) = dispatch(&data, &id, &tx, &mut malformed) else {
                continue;
            };
            if event.get("type").and_then(Value::as_str) == Some("done") {
                return Ok(());
            }
        }
        let _ = tx.send(ApiEvent::StreamEnded {
            session: id,
            malformed,
        });
        Ok(())
    }
}

fn dispatch(
    data: &str,
    session: &str,
    tx: &UnboundedSender<ApiEvent>,
    malformed: &mut usize,
) -> Option<Value> {
    match serde_json::from_str::<Value>(data) {
        Ok(event) => {
            let _ = tx.send(ApiEvent::Stream {
                session: session.to_string(),
                event: event.clone(),
            });
            Some(event)
        }
        Err(_) => {
            *malformed += 1;
            None
        }
    }
}

/// Incremental Server-Sent Events parser.
///
/// Handles LF/CRLF boundaries, `data:` with or without a space, multi-line
/// `data` fields, comment/keepalive lines, arbitrary network chunk splits
/// (including UTF-8 split across chunks), and a final event without a
/// trailing blank line.
#[derive(Default, Debug)]
pub(crate) struct SseParser {
    buf: Vec<u8>,
    data: Vec<String>,
}

impl SseParser {
    /// Feed raw bytes; returns one payload per completed event.
    pub(crate) fn feed(&mut self, chunk: &[u8]) -> Vec<String> {
        self.buf.extend_from_slice(chunk);
        let mut events = Vec::new();
        while let Some(pos) = self.buf.iter().position(|b| *b == b'\n') {
            let mut line = self.buf.drain(..=pos).collect::<Vec<u8>>();
            line.pop(); // '\n'
            if line.last() == Some(&b'\r') {
                line.pop();
            }
            // A complete line is valid UTF-8 per the SSE spec; decode lossily
            // only for stray bytes so one bad byte cannot kill the run.
            let line = String::from_utf8_lossy(&line).into_owned();
            if let Some(payload) = self.line(&line) {
                events.push(payload);
            }
        }
        events
    }

    /// Flush the trailing unterminated line and any pending event at EOF.
    pub(crate) fn finish(&mut self) -> Vec<String> {
        let mut events = Vec::new();
        if !self.buf.is_empty() {
            let line = String::from_utf8_lossy(&self.buf).into_owned();
            self.buf.clear();
            if let Some(payload) = self.line(&line) {
                events.push(payload);
            }
        }
        if let Some(payload) = self.dispatch() {
            events.push(payload);
        }
        events
    }

    fn line(&mut self, line: &str) -> Option<String> {
        if line.is_empty() {
            return self.dispatch();
        }
        if line.starts_with(':') {
            return None; // comment / keepalive
        }
        if let Some(value) = line.strip_prefix("data:") {
            // SSE allows "data:" with no space; strip at most one space.
            let value = value.strip_prefix(' ').unwrap_or(value);
            self.data.push(value.to_string());
        }
        // event:, id:, retry: are not used by this backend contract.
        None
    }

    fn dispatch(&mut self) -> Option<String> {
        if self.data.is_empty() {
            return None;
        }
        let payload = self.data.join("\n");
        self.data.clear();
        Some(payload)
    }
}

fn describe_transport_error(err: reqwest::Error) -> anyhow::Error {
    if err.is_timeout() {
        anyhow!("request timed out — the backend did not respond in time: {err}")
    } else if err.is_connect() {
        anyhow!("cannot reach the backend at the configured URL ({err}) — is it running?")
    } else if err.is_decode() {
        anyhow!("invalid response from backend: {err}")
    } else {
        anyhow!("backend request failed: {err}")
    }
}

/// Actionable message for an HTTP failure, including the backend's `detail`.
pub(crate) fn status_error(status: StatusCode, body: &str) -> anyhow::Error {
    let detail = detail(body);
    let hint = match status.as_u16() {
        400 => "the backend rejected the request — check the prompt, conversation id, and model selection",
        401 => "session expired or token invalid — sign in again with /login",
        403 => "you do not have access to this conversation",
        404 => "not found — the conversation may have been deleted or belong to another account",
        422 => "the request payload was rejected by the backend",
        429 => "rate limited by the backend — wait a moment and retry",
        500..=599 => "backend error — check the server logs and retry",
        _ => "request failed",
    };
    anyhow!("{hint} (HTTP {}): {detail}", status.as_u16())
}

fn detail(body: &str) -> String {
    serde_json::from_str::<Value>(body)
        .ok()
        .and_then(|v| v.get("detail").cloned())
        .map(|d| match d {
            Value::String(s) => s,
            other => other.to_string(),
        })
        .filter(|s| !s.is_empty())
        .unwrap_or_else(|| body.chars().take(200).collect())
}

#[cfg(test)]
mod tests {
    use super::{status_error, MessageRequest, SseParser};
    use reqwest::StatusCode;

    fn feed_all(parser: &mut SseParser, chunks: &[&str]) -> Vec<String> {
        let mut events = Vec::new();
        for chunk in chunks {
            events.extend(parser.feed(chunk.as_bytes()));
        }
        events
    }

    #[test]
    fn request_serializes_all_selected_models_and_web_search() {
        let body = MessageRequest {
            content: "question".into(),
            models: vec!["a/free".into(), "b/free".into()],
            web_search: true,
        };
        let json = serde_json::to_value(body).unwrap();
        assert_eq!(json["models"].as_array().unwrap().len(), 2);
        assert_eq!(json["models"][0], "a/free");
        assert_eq!(json["models"][1], "b/free");
        assert_eq!(json["web_search"], true);
        assert!(json.get("access_token").is_none());
    }

    #[test]
    fn sse_parses_events_split_across_chunks() {
        let mut parser = SseParser::default();
        let events = feed_all(
            &mut parser,
            &[
                "data: {\"type\":\"stage1_st",
                "art\"}\n\ndata: {\"type\":\"stage1_result\",\"model\":\"a\"}\n",
                "\n",
            ],
        );
        assert_eq!(events.len(), 2);
        assert_eq!(events[0], "{\"type\":\"stage1_start\"}");
        assert_eq!(events[1], "{\"type\":\"stage1_result\",\"model\":\"a\"}");
    }

    #[test]
    fn sse_handles_crlf_keepalives_and_missing_space() {
        let mut parser = SseParser::default();
        let events = feed_all(
            &mut parser,
            &[
                ": keepalive\r\n",
                "data:{\"type\":\"a\"}\r\n\r\n:ping\n",
                "\n",
                "data: {\"type\":\"done\"}\r\n\r\n",
            ],
        );
        assert_eq!(events, vec!["{\"type\":\"a\"}", "{\"type\":\"done\"}"]);
    }

    #[test]
    fn sse_joins_multiline_data_fields() {
        let mut parser = SseParser::default();
        let events = feed_all(&mut parser, &["data: {\"type\":\ndata: \"note\"}\n\n"]);
        assert_eq!(events, vec!["{\"type\":\n\"note\"}"]);
    }

    #[test]
    fn sse_flushes_final_event_without_trailing_blank_line() {
        let mut parser = SseParser::default();
        assert!(parser.feed(b"data: {\"type\":\"done\"}").is_empty());
        assert_eq!(parser.finish(), vec!["{\"type\":\"done\"}"]);
    }

    #[test]
    fn sse_preserves_multibyte_utf8_across_chunk_boundaries() {
        let mut parser = SseParser::default();
        let payload = "{\"type\":\"stage1_result\",\"response\":\"héllo — ✓\"}";
        let wire = format!("data: {payload}\n\n");
        let bytes = wire.as_bytes();
        // Split inside the first multibyte character.
        let split = wire.find('é').unwrap() + 1;
        let mut events = parser.feed(&bytes[..split]);
        events.extend(parser.feed(&bytes[split..]));
        assert_eq!(events, vec![payload]);
    }

    #[tokio::test]
    async fn stream_message_authorizes_and_settles_on_clean_eof() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let server = tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut buf = vec![0u8; 4096];
            let n = socket.read(&mut buf).await.unwrap();
            let head = String::from_utf8_lossy(&buf[..n]).to_lowercase();
            assert!(head.contains("post /api/conversations/conv/message/events"));
            assert!(head.contains("authorization: bearer test-token"));
            assert!(head.contains("accept: text/event-stream"));
            // One event then a clean EOF, deliberately without `done`.
            let body = "data: {\"type\":\"stage1_result\",\"model\":\"a/free\"}\n\n";
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            socket.write_all(response.as_bytes()).await.unwrap();
            socket.shutdown().await.unwrap();
        });

        let mut api = super::Api::new(format!("http://{addr}")).unwrap();
        api.set_token("test-token".into());
        let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel();
        api.stream_message(
            "conv".into(),
            "hello".into(),
            vec!["a/free".into()],
            false,
            tx,
        )
        .await
        .unwrap();
        server.await.unwrap();

        let mut saw_event = false;
        let mut ended = None;
        while let Ok(event) = rx.try_recv() {
            match event {
                super::ApiEvent::Stream { event, .. } => {
                    saw_event = true;
                    assert_eq!(event["model"], "a/free");
                }
                super::ApiEvent::StreamEnded { malformed, .. } => ended = Some(malformed),
                _ => {}
            }
        }
        assert!(saw_event);
        assert_eq!(ended, Some(0));
    }

    #[tokio::test]
    async fn stream_message_reports_http_status_with_hint() {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let (mut socket, _) = listener.accept().await.unwrap();
            let mut buf = vec![0u8; 4096];
            let _ = socket.read(&mut buf).await;
            let body = "{\"detail\":\"expired token\"}";
            let response = format!(
                "HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            let _ = socket.write_all(response.as_bytes()).await;
        });

        let mut api = super::Api::new(format!("http://{addr}")).unwrap();
        api.set_token("stale".into());
        let (tx, _rx) = tokio::sync::mpsc::unbounded_channel();
        let err = api
            .stream_message("conv".into(), "hello".into(), vec![], false, tx)
            .await
            .unwrap_err()
            .to_string();
        assert!(err.contains("401"));
        assert!(err.contains("sign in again"));
        assert!(err.contains("expired token"));
    }

    #[test]
    fn status_errors_are_actionable() {
        let msg = status_error(StatusCode::UNAUTHORIZED, "{\"detail\":\"expired\"}").to_string();
        assert!(msg.contains("401"));
        assert!(msg.contains("sign in again"));
        assert!(msg.contains("expired"));
        let msg = status_error(StatusCode::TOO_MANY_REQUESTS, "").to_string();
        assert!(msg.contains("429"));
        assert!(msg.contains("rate limited"));
        let msg = status_error(
            StatusCode::UNPROCESSABLE_ENTITY,
            "{\"detail\":[{\"loc\":1}]}",
        )
        .to_string();
        assert!(msg.contains("422"));
    }
}
