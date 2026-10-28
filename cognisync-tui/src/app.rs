use crate::api::{Api, ApiEvent, Health, Model, Session, Tools};
use serde_json::Value;
use std::collections::VecDeque;
use tokio::sync::mpsc::UnboundedSender;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Screen {
    Welcome,
    Login,
    Chat,
    History,
    Models,
    Tools,
    Settings,
    Help,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Overlay {
    Palette,
    None,
}

/// Execution status of an individual model result. Only states the backend
/// can actually justify are used: `Waiting` before `stage1_start`, `Thinking`
/// while the request is in flight, then `Complete` or `Failed` on its
/// `stage1_result`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ModelStatus {
    Waiting,
    Thinking,
    Complete,
    Failed,
}

impl ModelStatus {
    pub fn is_active(&self) -> bool {
        matches!(self, Self::Waiting | Self::Thinking)
    }
}

/// One entry of the backend's `rankings.aggregate` list. `average_rank` is
/// the backend's contract: lower is better.
#[derive(Clone, Debug, PartialEq)]
pub struct RankEntry {
    pub model_id: String,
    pub name: String,
    pub average_rank: f64,
    pub rankings_count: usize,
}

/// Unique best rank wins; equal best ranks are reported as a tie.
#[derive(Clone, Debug, PartialEq)]
pub enum RankingOutcome {
    None,
    Winner(RankEntry),
    Tie(Vec<RankEntry>),
}

pub fn ranking_outcome(entries: &[RankEntry]) -> RankingOutcome {
    let Some(best) = entries.iter().map(|e| e.average_rank).reduce(f64::min) else {
        return RankingOutcome::None;
    };
    let leaders: Vec<RankEntry> = entries
        .iter()
        .filter(|e| (e.average_rank - best).abs() < 0.005)
        .cloned()
        .collect();
    match leaders.len() {
        0 => RankingOutcome::None,
        1 => RankingOutcome::Winner(leaders[0].clone()),
        _ => RankingOutcome::Tie(leaders),
    }
}

/// One item of the conversation transcript. Entries keep the structure
/// delivered by the backend so model responses, peer reviews, rankings,
/// synthesis, sources and errors are never flattened into a single string.
#[derive(Clone, Debug)]
pub enum TranscriptEntry {
    User {
        text: String,
    },
    ModelResult {
        model: String,        // model id as advertised by the backend
        name: Option<String>, // human-readable model name from the catalog
        status: ModelStatus,
        text: String, // response body (empty while active or when it failed)
        #[allow(dead_code)] // otherwise test-only
        error: Option<String>,
        tokens: Option<usize>,
        duration_ms: Option<u64>,
    },
    PeerReview {
        model_id: String,
        name: Option<String>,
        success: bool,
        error: Option<String>,
        parsed_ranking: Vec<String>,
    },
    Rankings {
        entries: Vec<RankEntry>,
    },
    Synthesis {
        text: String,
        model: Option<String>,
        error: Option<String>,
    },
    Source {
        title: String,
        url: String,
    },
    Notice {
        text: String,
    },
}

pub struct App {
    pub screen: Screen,
    pub overlay: Overlay,
    pub input: String,
    pub palette: String,
    pub selected: usize,
    pub status: String,
    pub connected: bool,
    pub health: Option<Health>,
    pub session: Option<Session>,
    pub sessions: Vec<Session>,
    pub models: Vec<Model>,
    pub selected_models: Vec<String>,
    pub draft_models: Vec<String>,
    pub min_selection: usize,
    pub max_selection: usize,
    pub web_search: bool,
    pub tools: Option<Tools>,
    pub transcript: VecDeque<TranscriptEntry>,
    pub conversation_scroll: usize,
    pub loading: bool,
    pub thinking_dots: usize,
    /// Last `session`/`final` search metadata from the backend, rendered
    /// verbatim (never invented locally).
    pub search: Option<Value>,
    /// Terminal-event tracking for the current run.
    run_final_seen: bool,
    run_rankings_seen: bool,
    pub quit: bool,
    pub is_authenticated: bool,
    pub access_token: String,
    pub username: String,
    pub tx: UnboundedSender<ApiEvent>,
}
pub const COMMANDS: &[(&str, &str)] = &[
    ("/new", "Start a new session"),
    ("/history", "Browse saved conversations"),
    ("/models", "Select council models"),
    ("/search", "Toggle DuckDuckGo grounding"),
    ("/compare", "Show model responses in this chat"),
    ("/mcps", "Inspect backend tools"),
    ("/settings", "Backend settings"),
    ("/login", "Sign in or register"),
    ("/register", "Create an account"),
    ("/logout", "Sign out"),
    ("/help", "Show commands and shortcuts"),
    ("/exit", "Exit safely"),
];
impl App {
    pub fn new(tx: UnboundedSender<ApiEvent>) -> Self {
        Self {
            screen: Screen::Welcome,
            overlay: Overlay::None,
            input: String::new(),
            palette: String::new(),
            selected: 0,
            status: "Connecting…".into(),
            connected: false,
            health: None,
            session: None,
            sessions: vec![],
            models: vec![],
            selected_models: vec![],
            draft_models: vec![],
            min_selection: 1,
            max_selection: 4,
            web_search: false,
            tools: None,
            transcript: VecDeque::new(),
            conversation_scroll: 0,
            loading: false,
            thinking_dots: 0,
            search: None,
            run_final_seen: false,
            run_rankings_seen: false,
            quit: false,
            is_authenticated: false,
            access_token: String::new(),
            username: String::new(),
            tx,
        }
    }
    pub fn command_matches(&self) -> Vec<(&'static str, &'static str)> {
        let q = self.palette.to_lowercase();
        COMMANDS
            .iter()
            .copied()
            .filter(|(c, d)| q.is_empty() || c.contains(&q) || d.to_lowercase().contains(&q))
            .collect()
    }
    pub fn begin_model_picker(&mut self) {
        self.draft_models = self.selected_models.clone();
        self.selected = 0;
        self.screen = Screen::Models;
        self.status = "Loading model catalog…".into();
    }
    pub fn toggle_draft_model(&mut self) {
        let Some(model) = self.models.get(self.selected) else {
            return;
        };
        if let Some(index) = self.draft_models.iter().position(|id| id == &model.id) {
            self.draft_models.remove(index);
        } else if self.draft_models.len() >= self.max_selection {
            self.status = format!("At most {} models can be selected", self.max_selection);
        } else {
            self.draft_models.push(model.id.clone());
        }
    }
    pub fn confirm_model_picker(&mut self) -> bool {
        if self.draft_models.len() < self.min_selection {
            self.status = format!(
                "Select at least {} model before confirming",
                self.min_selection
            );
            return false;
        }
        self.selected_models = self.draft_models.clone();
        self.status = format!("{} council models selected", self.selected_models.len());
        self.screen = if self.session.is_some() {
            Screen::Chat
        } else {
            Screen::Welcome
        };
        true
    }
    pub fn cancel_model_picker(&mut self) {
        self.draft_models = self.selected_models.clone();
        self.status = "Model selection unchanged".into();
        self.screen = if self.session.is_some() {
            Screen::Chat
        } else {
            Screen::Welcome
        };
    }
    pub fn toggle_web_search(&mut self) {
        self.web_search = !self.web_search;
        self.status = if self.web_search {
            "DuckDuckGo grounding enabled for the next prompt".into()
        } else {
            "DuckDuckGo grounding disabled".into()
        };
    }
    /// Jump the conversation view to the most recent content.
    pub fn follow_bottom(&mut self) {
        self.conversation_scroll = usize::MAX;
    }
    /// Start a run from the current selection: the user turn plus one
    /// `Waiting` card per selected model. Nothing is marked thinking until
    /// the backend's `stage1_start` confirms the run began.
    pub fn begin_run(&mut self, content: String) {
        self.transcript
            .push_back(TranscriptEntry::User { text: content });
        let selected = self.selected_models.clone();
        for id in selected {
            let name = self.model_name(&id);
            self.transcript.push_back(TranscriptEntry::ModelResult {
                model: id,
                name,
                status: ModelStatus::Waiting,
                text: String::new(),
                error: None,
                tokens: None,
                duration_ms: None,
            });
        }
        self.loading = true;
        self.run_final_seen = false;
        self.run_rankings_seen = false;
        self.search = None;
        self.status = "Sending prompt…".into();
        self.follow_bottom();
    }
    /// Animation tick. The event loop only calls this while `loading`.
    pub fn tick(&mut self) {
        if self.loading {
            self.thinking_dots = self.thinking_dots.wrapping_add(1);
        }
    }
    pub fn fetch_bootstrap(&self, api: Api) {
        let tx = self.tx.clone();
        tokio::spawn(async move {
            let _ = tx.send(ApiEvent::Health(
                api.health().await.map_err(|e| e.to_string()),
            ));
        });
    }
    pub fn handle(&mut self, ev: ApiEvent) {
        match ev {
            ApiEvent::Health(r) => match r {
                Ok(h) => {
                    self.connected = true;
                    self.status = "Connected".into();
                    self.health = Some(h)
                }
                Err(e) => self.status = format!("Offline: {e}"),
            },
            ApiEvent::History(r) => match r {
                Ok(x) => {
                    self.sessions = x;
                    self.status = format!("{} saved conversations", self.sessions.len())
                }
                Err(e) => self.status = e,
            },
            ApiEvent::Open(r) => match r {
                Ok(s) => self.load_session(s),
                Err(e) => self.status = e,
            },
            ApiEvent::Created(r) => match r {
                Ok(s) => {
                    self.session = Some(s);
                    self.transcript.clear();
                    self.loading = false;
                    self.run_final_seen = false;
                    self.run_rankings_seen = false;
                    self.conversation_scroll = 0;
                    self.screen = Screen::Chat;
                    self.status = "New session".into();
                }
                Err(e) => self.status = e,
            },
            ApiEvent::Models(r) => match r {
                Ok(x) => {
                    self.models = x.models;
                    self.min_selection = x.min_selection;
                    self.max_selection = x.max_selection.max(x.min_selection);
                    self.draft_models
                        .retain(|id| self.models.iter().any(|model| &model.id == id));
                    self.status = if self.models.is_empty() {
                        "No eligible models are available".into()
                    } else {
                        "Use ↑/↓ and Space; Enter confirms; Esc cancels".into()
                    }
                }
                Err(e) => self.status = e,
            },
            ApiEvent::Tools(r) => match r {
                Ok(x) => {
                    self.tools = Some(x);
                    self.status = "Tool status loaded".into()
                }
                Err(e) => self.status = e,
            },
            ApiEvent::Stream { session, event } => {
                if self.session.as_ref().map(|s| &s.id) != Some(&session) {
                    return;
                }
                self.apply_stream(event);
            }
            ApiEvent::StreamEnded { session, malformed } => {
                if self.session.as_ref().map(|s| &s.id) != Some(&session) {
                    return;
                }
                self.loading = false;
                self.fail_active_models("No result was received before the stream ended.");
                if malformed > 0 {
                    self.transcript.push_back(TranscriptEntry::Notice {
                        text: format!("{malformed} malformed stream event(s) were ignored."),
                    });
                }
                if !self.run_final_seen {
                    self.transcript.push_back(TranscriptEntry::Notice {
                        text:
                            "Stream ended before the backend sent a final answer (protocol error)."
                                .into(),
                    });
                    self.status = "Stream ended without a final answer".into();
                } else {
                    self.status = "Council response saved".into();
                }
                self.follow_bottom();
            }
            ApiEvent::Login(r) => match r {
                Ok(auth) => self.load_auth(auth),
                Err(e) => {
                    self.loading = false;
                    self.status = e;
                }
            },
            ApiEvent::Error(e) => {
                // A transport/HTTP failure mid-run must settle the cards and
                // leave the error in the transcript, not only the status bar.
                if self.loading {
                    self.fail_active_models(&e);
                    self.transcript
                        .push_back(TranscriptEntry::Notice { text: e.clone() });
                    self.follow_bottom();
                }
                self.loading = false;
                self.status = e
            }
        }
    }
    fn load_auth(&mut self, auth: crate::api::AuthResponse) {
        self.is_authenticated = true;
        self.access_token = auth.access_token;
        self.username = auth.username.clone();
        self.screen = Screen::Welcome;
        self.status = format!("Signed in as {}", auth.username);
    }
    /// Human-readable name for a model id, from the loaded catalog when known.
    fn model_name(&self, model_id: &str) -> Option<String> {
        self.models
            .iter()
            .find(|m| m.id == model_id)
            .map(|m| m.name.clone())
    }
    fn load_session(&mut self, s: Session) {
        self.transcript.clear();
        self.loading = false;
        self.run_final_seen = false;
        self.run_rankings_seen = false;
        self.search = None;
        for m in &s.messages {
            match m.get("role").and_then(Value::as_str) {
                Some("user") => {
                    if let Some(c) = m.get("content").and_then(Value::as_str) {
                        self.transcript.push_back(TranscriptEntry::User {
                            text: parse_content(c),
                        });
                    }
                }
                Some("assistant") => self.load_assistant_message(m),
                _ => {}
            }
        }
        self.session = Some(s);
        self.screen = Screen::Chat;
        self.status = "Conversation opened".into();
        self.follow_bottom();
    }

    /// Restore one persisted assistant turn (stage 1, peer review, rankings,
    /// synthesis, sources) without inventing anything the backend omitted.
    fn load_assistant_message(&mut self, m: &Value) {
        if let Some(stage1) = m.get("stage1").and_then(Value::as_array) {
            for entry in stage1 {
                let model_id = entry
                    .get("model_id")
                    .and_then(Value::as_str)
                    .unwrap_or_else(|| entry.get("model").and_then(Value::as_str).unwrap_or(""))
                    .to_string();
                let name = entry
                    .get("model")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                let success = entry
                    .get("success")
                    .and_then(Value::as_bool)
                    .unwrap_or(false);
                let response = entry
                    .get("response")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                let error = entry
                    .get("error")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                let duration_ms = entry.get("duration_ms").and_then(Value::as_u64);
                let tokens = entry
                    .get("tokens")
                    .and_then(Value::as_u64)
                    .map(|t| t as usize);
                self.transcript.push_back(TranscriptEntry::ModelResult {
                    model: model_id,
                    name,
                    status: if success {
                        ModelStatus::Complete
                    } else {
                        ModelStatus::Failed
                    },
                    text: if response.is_empty() {
                        error.clone().unwrap_or_else(|| "No response.".into())
                    } else {
                        response
                    },
                    error,
                    tokens,
                    duration_ms,
                });
            }
        }

        if let Some(stage2) = m.get("stage2").and_then(Value::as_array) {
            for entry in stage2 {
                let model_id = entry
                    .get("model_id")
                    .and_then(Value::as_str)
                    .unwrap_or_else(|| entry.get("model").and_then(Value::as_str).unwrap_or(""))
                    .to_string();
                let name = entry
                    .get("model")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                let parsed_ranking = entry
                    .get("parsed_ranking")
                    .and_then(Value::as_array)
                    .map(|a| {
                        a.iter()
                            .filter_map(Value::as_str)
                            .map(str::to_owned)
                            .collect()
                    })
                    .unwrap_or_default();
                self.transcript.push_back(TranscriptEntry::PeerReview {
                    model_id,
                    name,
                    success: entry
                        .get("success")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    error: entry
                        .get("error")
                        .and_then(Value::as_str)
                        .map(str::to_owned),
                    parsed_ranking,
                });
            }
        }

        let metadata = m.get("metadata");
        if let Some(search) = metadata.and_then(|md| md.get("search")) {
            self.search = Some(search.clone());
        }
        if let Some(aggregate) = metadata
            .and_then(|md| md.get("aggregate_rankings"))
            .and_then(Value::as_array)
        {
            let entries = self.rank_entries(aggregate);
            if !entries.is_empty() {
                self.transcript
                    .push_back(TranscriptEntry::Rankings { entries });
            }
        }

        let model = metadata
            .and_then(|md| md.get("final_answer_model_id"))
            .and_then(Value::as_str)
            .map(str::to_owned)
            .or_else(|| {
                m.get("stage3")
                    .and_then(Value::as_object)
                    .and_then(|s| s.get("model"))
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            });
        let model = model.map(|id| self.model_name(&id).unwrap_or(id));

        if let Some(a) = m.get("final_answer").and_then(Value::as_str) {
            self.transcript.push_back(TranscriptEntry::Synthesis {
                text: a.to_string(),
                model,
                error: None,
            });
        } else if let Some(e) = m.get("final_answer_error").and_then(Value::as_str) {
            self.transcript.push_back(TranscriptEntry::Synthesis {
                text: String::new(),
                model,
                error: Some(e.to_string()),
            });
        } else if let Some(stage3) = m.get("stage3").and_then(Value::as_object) {
            if let Some(response) = stage3.get("response").and_then(Value::as_str) {
                let success = stage3
                    .get("success")
                    .and_then(Value::as_bool)
                    .unwrap_or(true);
                if success {
                    self.transcript.push_back(TranscriptEntry::Synthesis {
                        text: response.to_string(),
                        model,
                        error: None,
                    });
                } else {
                    self.transcript.push_back(TranscriptEntry::Synthesis {
                        text: String::new(),
                        model,
                        error: stage3
                            .get("error")
                            .and_then(Value::as_str)
                            .map(str::to_owned)
                            .or_else(|| Some("Chairman synthesis is unavailable.".into())),
                    });
                }
            }
        }

        if let Some(sources) = m.get("sources").and_then(Value::as_array) {
            self.push_sources(sources);
        }
    }

    fn rank_entries(&self, aggregate: &[Value]) -> Vec<RankEntry> {
        aggregate
            .iter()
            .filter_map(|a| {
                let model_id = a.get("model").and_then(Value::as_str)?;
                Some(RankEntry {
                    model_id: model_id.to_string(),
                    name: self.model_name(model_id).unwrap_or_else(|| model_id.into()),
                    average_rank: a.get("average_rank").and_then(Value::as_f64)?,
                    rankings_count: a.get("rankings_count").and_then(Value::as_u64).unwrap_or(0)
                        as usize,
                })
            })
            .collect()
    }

    fn push_sources(&mut self, sources: &[Value]) {
        for source in sources {
            if let (Some(title), Some(url)) = (
                source.get("title").and_then(Value::as_str),
                source.get("url").and_then(Value::as_str),
            ) {
                self.transcript.push_back(TranscriptEntry::Source {
                    title: title.to_string(),
                    url: url.to_string(),
                });
            }
        }
    }

    /// Mark every still-active model as failed with an honest reason.
    fn fail_active_models(&mut self, reason: &str) {
        for entry in self.transcript.iter_mut().rev() {
            if let TranscriptEntry::ModelResult {
                status,
                text,
                error,
                ..
            } = entry
            {
                if status.is_active() {
                    *status = ModelStatus::Failed;
                    if error.is_none() {
                        *error = Some(reason.to_string());
                    }
                    if text.is_empty() {
                        *text = reason.to_string();
                    }
                }
            }
        }
    }

    fn upsert_model_thinking(&mut self, id: String, name: Option<String>) {
        let name = name.or_else(|| self.model_name(&id));
        if let Some(TranscriptEntry::ModelResult {
            name: entry_name,
            status,
            ..
        }) = self.transcript.iter_mut().rev().find(|e| {
            matches!(e, TranscriptEntry::ModelResult { model, status, .. } if model == &id && status.is_active())
        }) {
            *status = ModelStatus::Thinking;
            if entry_name.is_none() {
                *entry_name = name;
            }
            return;
        }
        self.transcript.push_back(TranscriptEntry::ModelResult {
            model: id,
            name,
            status: ModelStatus::Thinking,
            text: String::new(),
            error: None,
            tokens: None,
            duration_ms: None,
        });
    }

    fn apply_stage1_result(&mut self, event: &Value) {
        let model = event
            .get("model")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        if model.is_empty() {
            return;
        }
        let success = event
            .get("success")
            .and_then(Value::as_bool)
            .unwrap_or(false);
        let response = event
            .get("response")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let error = event
            .get("error")
            .and_then(Value::as_str)
            .map(str::to_owned);
        let tokens = event
            .get("tokens")
            .and_then(Value::as_u64)
            .map(|t| t as usize);
        let duration_ms = event.get("duration_ms").and_then(Value::as_u64);
        let name = self.model_name(&model);
        let status = if success {
            ModelStatus::Complete
        } else {
            ModelStatus::Failed
        };
        let text = if response.is_empty() {
            error.clone().unwrap_or_else(|| "No response.".into())
        } else {
            response
        };

        if let Some(TranscriptEntry::ModelResult {
            status: current,
            text: body,
            error: err,
            tokens: tok,
            duration_ms: dur,
            name: entry_name,
            ..
        }) =
            self.transcript.iter_mut().rev().find(
                |e| matches!(e, TranscriptEntry::ModelResult { model: id, .. } if id == &model),
            )
        {
            // A replayed/duplicate terminal result must not overwrite a card.
            if current.is_active() {
                *current = status;
                *body = text;
                *err = error;
                *tok = tokens;
                *dur = duration_ms;
                if entry_name.is_none() {
                    *entry_name = name;
                }
            }
            return;
        }
        self.transcript.push_back(TranscriptEntry::ModelResult {
            model,
            name,
            status,
            text,
            error,
            tokens,
            duration_ms,
        });
    }

    fn apply_stream(&mut self, event: Value) {
        let kind = event.get("type").and_then(Value::as_str).unwrap_or("");
        match kind {
            "session" => {
                self.loading = true;
                if let Some(search) = event.get("web_search") {
                    self.search = Some(search.clone());
                    if let Some(note) = search_summary(search) {
                        self.transcript
                            .push_back(TranscriptEntry::Notice { text: note });
                    }
                }
                self.status = "Council run started".into();
            }
            "note" => {
                if let Some(message) = event.get("message").and_then(Value::as_str) {
                    self.transcript.push_back(TranscriptEntry::Notice {
                        text: message.to_string(),
                    });
                }
            }
            "stage1_start" => {
                self.loading = true;
                let names = event.get("names").and_then(Value::as_object);
                let ids: Vec<String> = event
                    .get("models")
                    .and_then(Value::as_array)
                    .map(|a| {
                        a.iter()
                            .filter_map(Value::as_str)
                            .map(str::to_owned)
                            .collect()
                    })
                    .unwrap_or_default();
                let count = ids.len();
                for id in ids {
                    let name = names
                        .and_then(|n| n.get(&id))
                        .and_then(Value::as_str)
                        .map(str::to_owned);
                    self.upsert_model_thinking(id, name);
                }
                self.status = format!("Stage 1 · {count} models responding");
                self.follow_bottom();
            }
            "stage1_result" => {
                self.loading = true;
                self.apply_stage1_result(&event);
                self.status = "Stage 1 · collecting model responses".into();
                self.follow_bottom();
            }
            "stage2_start" => {
                self.loading = true;
                let count = event
                    .get("reviewers")
                    .and_then(Value::as_array)
                    .map(Vec::len)
                    .unwrap_or(0);
                self.status = format!("Stage 2 · peer review by {count} models");
            }
            "stage2_result" => {
                let model_id = event
                    .get("model")
                    .and_then(Value::as_str)
                    .unwrap_or("")
                    .to_string();
                let name = self.model_name(&model_id);
                let parsed_ranking = event
                    .get("parsed_ranking")
                    .and_then(Value::as_array)
                    .map(|a| {
                        a.iter()
                            .filter_map(Value::as_str)
                            .map(str::to_owned)
                            .collect()
                    })
                    .unwrap_or_default();
                self.transcript.push_back(TranscriptEntry::PeerReview {
                    model_id,
                    name,
                    success: event
                        .get("success")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    error: event
                        .get("error")
                        .and_then(Value::as_str)
                        .map(str::to_owned),
                    parsed_ranking,
                });
                self.status = "Stage 2 · review received".into();
                self.follow_bottom();
            }
            "rankings" => {
                let entries = event
                    .get("aggregate")
                    .and_then(Value::as_array)
                    .map(|a| self.rank_entries(a))
                    .unwrap_or_default();
                if !entries.is_empty() {
                    self.run_rankings_seen = true;
                    self.transcript
                        .push_back(TranscriptEntry::Rankings { entries });
                }
                self.status = "Stage 2 · rankings ready".into();
                self.follow_bottom();
            }
            "stage3_start" => {
                self.loading = true;
                self.status = "Stage 3 · chairman synthesizing".into();
            }
            "stage3_result" => {
                self.status = "Stage 3 · synthesis received".into();
            }
            "final" => {
                self.loading = false;
                self.run_final_seen = true;
                self.apply_final(&event);
                self.status = "Council response saved".into();
                self.follow_bottom();
            }
            "done" => {
                self.loading = false;
                self.fail_active_models("No result was received before the run ended.");
                if !self.run_final_seen {
                    self.transcript.push_back(TranscriptEntry::Notice {
                        text: "Run finished without a final answer (protocol error).".into(),
                    });
                    self.status = "Run finished without a final answer".into();
                }
                self.follow_bottom();
            }
            "error" => {
                self.loading = false;
                let message = event
                    .get("message")
                    .and_then(Value::as_str)
                    .or_else(|| event.get("error").and_then(Value::as_str))
                    .unwrap_or("Council failed")
                    .to_string();
                self.fail_active_models(&message);
                self.transcript.push_back(TranscriptEntry::Notice {
                    text: message.clone(),
                });
                self.status = message;
                self.follow_bottom();
            }
            "run_failed" => {
                // The backend follows with `final` + `done`; do not clear
                // loading here so the terminal events settle the run.
                self.loading = true;
                let message = event
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("All council model requests failed.");
                self.transcript.push_back(TranscriptEntry::Notice {
                    text: message.to_string(),
                });
                self.status = message.to_string();
                self.follow_bottom();
            }
            _ => {}
        }
    }

    fn apply_final(&mut self, event: &Value) {
        let Some(payload) = event.get("payload").and_then(Value::as_object) else {
            self.transcript.push_back(TranscriptEntry::Notice {
                text: "Final event had no payload (protocol error).".into(),
            });
            return;
        };
        self.fail_active_models("No result was received before the run ended.");

        if let Some(search) = payload
            .get("metadata")
            .and_then(Value::as_object)
            .and_then(|m| m.get("search"))
        {
            self.search = Some(search.clone());
        }

        let model_id = payload
            .get("metadata")
            .and_then(Value::as_object)
            .and_then(|m| m.get("final_answer_model_id"))
            .and_then(Value::as_str)
            .map(str::to_owned)
            .or_else(|| {
                payload
                    .get("final_answer_model")
                    .and_then(Value::as_str)
                    .map(str::to_owned)
            });
        let model = model_id.map(|id| self.model_name(&id).unwrap_or(id));

        if !self.run_rankings_seen {
            let entries = payload
                .get("metadata")
                .and_then(Value::as_object)
                .and_then(|m| m.get("aggregate_rankings"))
                .and_then(Value::as_array)
                .map(|a| self.rank_entries(a))
                .unwrap_or_default();
            if !entries.is_empty() {
                self.run_rankings_seen = true;
                self.transcript
                    .push_back(TranscriptEntry::Rankings { entries });
            }
        }

        match payload.get("final_answer").and_then(Value::as_str) {
            Some(answer) if !answer.trim().is_empty() => {
                self.transcript.push_back(TranscriptEntry::Synthesis {
                    text: answer.to_string(),
                    model,
                    error: None,
                });
            }
            _ => {
                let error = payload
                    .get("final_answer_error")
                    .and_then(Value::as_str)
                    .map(str::to_owned);
                if let Some(error) = error {
                    self.transcript.push_back(TranscriptEntry::Synthesis {
                        text: String::new(),
                        model,
                        error: Some(error),
                    });
                } else {
                    self.transcript.push_back(TranscriptEntry::Notice {
                        text:
                            "Final event contained neither an answer nor an error (protocol error)."
                                .into(),
                    });
                }
            }
        }

        if let Some(sources) = payload.get("sources").and_then(Value::as_array) {
            self.push_sources(sources);
        }
    }
}

/// Human-readable summary of the backend's search metadata, or `None` when
/// search was not enabled. Never claims success the backend did not report.
fn search_summary(search: &Value) -> Option<String> {
    if search.get("enabled").and_then(Value::as_bool) != Some(true) {
        return None;
    }
    if let Some(err) = search.get("error_message").and_then(Value::as_str) {
        return Some(format!("Web search failed: {err}"));
    }
    let status = search
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("unknown");
    let count = search
        .get("result_count")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    if status == "success" {
        Some(format!(
            "Web search returned {count} source{}.",
            if count == 1 { "" } else { "s" }
        ))
    } else {
        Some(format!("Web search finished with status {status}."))
    }
}

pub(crate) fn parse_content(s: &str) -> String {
    serde_json::from_str::<Value>(s)
        .ok()
        .and_then(|v| v.get("content").and_then(Value::as_str).map(str::to_owned))
        .unwrap_or_else(|| s.into())
}

#[cfg(test)]
mod tests {
    use super::*;
    use tokio::sync::mpsc;

    fn model(id: &str) -> Model {
        Model {
            id: id.into(),
            name: id.into(),
            free: true,
        }
    }
    fn app() -> App {
        let (tx, _) = mpsc::unbounded_channel();
        App::new(tx)
    }
    fn session(id: &str) -> Session {
        Session {
            id: id.into(),
            title: "t".into(),
            created_at: "".into(),
            updated_at: "".into(),
            message_count: 0,
            messages: vec![],
        }
    }
    fn stage1_event(model: &str, success: bool, response: Value, error: Value) -> Value {
        serde_json::json!({
            "type": "stage1_result",
            "model": model,
            "success": success,
            "response": response,
            "error": error,
            "duration_ms": 100,
            "tokens": 10,
            "error_category": null,
            "fallback_used": null,
        })
    }

    #[test]
    fn login_event_upgrades_to_authenticated() {
        let mut app = app();
        app.handle(ApiEvent::Login(Ok(crate::api::AuthResponse {
            access_token: "jwt".into(),
            expires_in: 3600,
            user_id: "u1".into(),
            username: "tester".into(),
        })));
        assert!(app.is_authenticated);
        assert_eq!(app.access_token, "jwt");
        assert_eq!(app.username, "tester");
        assert_eq!(app.screen, Screen::Welcome);
        assert_eq!(app.status, "Signed in as tester");
        app.handle(ApiEvent::Login(Err("bad credentials".into())));
        assert_eq!(app.status, "bad credentials"); // error keeps prior auth state
        assert!(app.is_authenticated);
    }

    #[test]
    fn multi_select_is_deterministic_and_survives_cancel() {
        let mut app = app();
        app.models = vec![model("a"), model("b"), model("c")];
        app.begin_model_picker();
        app.toggle_draft_model();
        app.selected = 1;
        app.toggle_draft_model();
        assert_eq!(app.draft_models, vec!["a", "b"]);
        assert!(app.confirm_model_picker());
        assert_eq!(app.selected_models, vec!["a", "b"]);
        app.begin_model_picker();
        app.selected = 2;
        app.toggle_draft_model();
        app.cancel_model_picker();
        assert_eq!(app.selected_models, vec!["a", "b"]);
    }
    #[test]
    fn empty_and_over_limit_selection_cannot_be_confirmed_or_added() {
        let mut app = app();
        app.models = vec![model("a"), model("b")];
        app.max_selection = 1;
        app.begin_model_picker();
        assert!(!app.confirm_model_picker());
        app.toggle_draft_model();
        app.selected = 1;
        app.toggle_draft_model();
        assert_eq!(app.draft_models, vec!["a"]);
        assert!(app.confirm_model_picker());
    }
    #[test]
    fn failed_stage1_result_is_recorded_without_erasing_success() {
        let mut app = app();
        app.session = Some(session("s"));
        app.models = vec![model("a/free"), model("b/free")];
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: stage1_event("a/free", true, Value::String("ok".into()), Value::Null),
        });
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: stage1_event("b/free", false, Value::Null, Value::String("boom".into())),
        });
        assert_eq!(app.transcript.len(), 2);
        match &app.transcript[0] {
            TranscriptEntry::ModelResult {
                status: ModelStatus::Complete,
                text,
                ..
            } => {
                assert_eq!(text, "ok");
            }
            other => panic!("expected complete model result, got {other:?}"),
        }
        match &app.transcript[1] {
            TranscriptEntry::ModelResult {
                status: ModelStatus::Failed,
                error: Some(e),
                text,
                ..
            } => {
                assert_eq!(e, "boom");
                assert_eq!(text, "boom");
            }
            other => panic!("expected failed model result, got {other:?}"),
        }
        assert_eq!(app.transcript.len(), 2);
        assert!(app.loading); // mid-stream loading stays on until "done"
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({"type": "done"}),
        });
        assert!(!app.loading);
    }
    #[test]
    fn final_answer_is_stored_as_separate_synthesis() {
        let mut app = app();
        app.session = Some(session("s"));
        app.models = vec![model("a/free")];
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({
                "type": "final",
                "payload": {
                    "final_answer": "the synthesis",
                    "final_answer_model": "Model A",
                    "metadata": { "final_answer_model_id": "a/free" },
                    "sources": [
                        {"title": "Example", "url": "https://example.com"}
                    ],
                },
            }),
        });
        assert!(!app.loading);
        assert!(app.run_final_seen);
        assert_eq!(app.transcript.len(), 2);
        match &app.transcript[0] {
            TranscriptEntry::Synthesis { text, model, error } => {
                assert_eq!(text, "the synthesis");
                assert_eq!(model.as_deref(), Some("a/free"));
                assert!(error.is_none());
            }
            other => panic!("expected synthesis, got {other:?}"),
        }
        match &app.transcript[1] {
            TranscriptEntry::Source { title, url } => {
                assert_eq!(title, "Example");
                assert_eq!(url, "https://example.com");
            }
            other => panic!("expected source, got {other:?}"),
        }
    }
    #[test]
    fn synthesis_error_is_not_fabricated() {
        let mut app = app();
        app.session = Some(session("s"));
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({
                "type": "final",
                "payload": {
                    "final_answer_error": "chairman timed out",
                },
            }),
        });
        match &app.transcript[0] {
            TranscriptEntry::Synthesis {
                text,
                error: Some(e),
                ..
            } => {
                assert_eq!(text, "");
                assert_eq!(e, "chairman timed out");
            }
            other => panic!("expected failed synthesis, got {other:?}"),
        }
    }
    #[test]
    fn run_failed_keeps_successful_stage1_results() {
        let mut app = app();
        app.session = Some(session("s"));
        app.models = vec![model("a/free")];
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: stage1_event("a/free", true, Value::String("fine".into()), Value::Null),
        });
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({
                "type": "run_failed",
                "error": "All council model requests failed.",
                "stage1": [],
            }),
        });
        assert_eq!(app.transcript.len(), 2);
        // `run_failed` is followed by `final` + `done`; the run settles there.
        assert!(app.loading);
        assert_eq!(app.status, "All council model requests failed.");
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({"type": "done"}),
        });
        assert!(!app.loading);
        assert!(!app.run_final_seen);
    }
    #[test]
    fn events_for_other_session_are_ignored() {
        let mut app = app();
        app.session = Some(session("s"));
        app.handle(ApiEvent::Stream {
            session: "other".into(),
            event: stage1_event("a/free", true, Value::String("stale".into()), Value::Null),
        });
        assert!(app.transcript.is_empty());
    }
    #[test]
    fn load_session_restructures_stored_stage1_results() {
        let mut app = app();
        app.models = vec![Model {
            id: "cohere/north-mini-code:free".into(),
            name: "Cohere: North Mini Code".into(),
            free: true,
        }];
        app.load_session(Session {
            id: "s".into(),
            title: "t".into(),
            created_at: "".into(),
            updated_at: "".into(),
            message_count: 2,
            messages: vec![
                serde_json::json!({
                    "role": "user",
                    "content": "{\"role\":\"user\",\"content\":\"Write a basic Python program.\",\"web_search\":false}",
                    "created_at": "2026-10-27T17:01:36Z",
                }),
                serde_json::json!({
                    "role": "assistant",
                    "stage1": [
                        {
                            "type": "stage1_result",
                            "model": "Cohere: North Mini Code",
                            "model_id": "cohere/north-mini-code:free",
                            "success": true,
                            "response": "```python\nprint(\"hi\")\n```",
                            "error": null,
                            "duration_ms": 2916,
                            "tokens": 140,
                        },
                        {
                            "type": "stage1_result",
                            "model": "Other: Model",
                            "model_id": "other/model:free",
                            "success": false,
                            "response": null,
                            "error": "rate limited",
                            "duration_ms": 0,
                            "tokens": 0,
                        },
                    ],
                    "final_answer": "Both say hi.",
                    "final_answer_error": null,
                }),
            ],
        });
        assert_eq!(app.transcript.len(), 4);
        match &app.transcript[0] {
            TranscriptEntry::User { text } => assert_eq!(text, "Write a basic Python program."),
            other => panic!("expected user entry, got {other:?}"),
        }
        match &app.transcript[1] {
            TranscriptEntry::ModelResult {
                model,
                name,
                status: ModelStatus::Complete,
                text,
                ..
            } => {
                assert_eq!(model, "cohere/north-mini-code:free");
                assert_eq!(name.as_deref(), Some("Cohere: North Mini Code"));
                assert_eq!(text, "```python\nprint(\"hi\")\n```");
            }
            other => panic!("expected complete model result, got {other:?}"),
        }
        match &app.transcript[2] {
            TranscriptEntry::ModelResult {
                status: ModelStatus::Failed,
                error: Some(e),
                ..
            } => assert_eq!(e, "rate limited"),
            other => panic!("expected failed model result, got {other:?}"),
        }
        match &app.transcript[3] {
            TranscriptEntry::Synthesis { text, .. } => assert_eq!(text, "Both say hi."),
            other => panic!("expected synthesis, got {other:?}"),
        }
    }

    fn model_states(app: &App) -> Vec<(String, ModelStatus)> {
        app.transcript
            .iter()
            .filter_map(|e| match e {
                TranscriptEntry::ModelResult { model, status, .. } => {
                    Some((model.clone(), status.clone()))
                }
                _ => None,
            })
            .collect()
    }

    #[test]
    fn begin_run_marks_selected_models_waiting() {
        let mut app = app();
        app.models = vec![model("a/free")];
        app.selected_models = vec!["a/free".into(), "b/free".into()];
        app.begin_run("hello".into());
        assert!(app.loading);
        assert_eq!(app.transcript.len(), 3);
        match &app.transcript[0] {
            TranscriptEntry::User { text } => assert_eq!(text, "hello"),
            other => panic!("expected user entry, got {other:?}"),
        }
        assert_eq!(
            model_states(&app),
            vec![
                ("a/free".to_string(), ModelStatus::Waiting),
                ("b/free".to_string(), ModelStatus::Waiting),
            ]
        );
    }

    #[test]
    fn stage1_events_update_cards_in_place() {
        let mut app = app();
        app.session = Some(session("s"));
        app.selected_models = vec!["a/free".into(), "b/free".into()];
        app.begin_run("q".into());
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({
                "type": "stage1_start",
                "models": ["a/free", "b/free"],
                "names": {"a/free": "Model A", "b/free": "Model B"},
            }),
        });
        assert_eq!(
            model_states(&app),
            vec![
                ("a/free".to_string(), ModelStatus::Thinking),
                ("b/free".to_string(), ModelStatus::Thinking),
            ]
        );
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: stage1_event("a/free", true, Value::String("answer".into()), Value::Null),
        });
        assert_eq!(app.transcript.len(), 3); // no duplicate card
        match &app.transcript[1] {
            TranscriptEntry::ModelResult {
                name,
                status: ModelStatus::Complete,
                text,
                tokens,
                duration_ms,
                ..
            } => {
                assert_eq!(name.as_deref(), Some("Model A"));
                assert_eq!(text, "answer");
                assert_eq!(*tokens, Some(10));
                assert_eq!(*duration_ms, Some(100));
            }
            other => panic!("expected completed model, got {other:?}"),
        }
        // Replayed terminal event must not overwrite the card.
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: stage1_event("a/free", false, Value::Null, Value::String("late".into())),
        });
        assert_eq!(app.transcript.len(), 3);
        match &app.transcript[1] {
            TranscriptEntry::ModelResult {
                status: ModelStatus::Complete,
                text,
                ..
            } => assert_eq!(text, "answer"),
            other => panic!("expected unchanged completed model, got {other:?}"),
        }
    }

    #[test]
    fn stage2_and_rankings_are_recorded_from_backend_events() {
        let mut app = app();
        app.session = Some(session("s"));
        app.models = vec![model("a/free"), model("b/free")];
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({
                "type": "stage2_result",
                "model": "a/free",
                "success": true,
                "review": "review text",
                "parsed_ranking": ["Response B", "Response A"],
            }),
        });
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({
                "type": "rankings",
                "aggregate": [
                    {"model": "b/free", "average_rank": 1.0, "rankings_count": 2},
                    {"model": "a/free", "average_rank": 2.0, "rankings_count": 2},
                ],
            }),
        });
        let reviews: Vec<_> = app
            .transcript
            .iter()
            .filter_map(|e| match e {
                TranscriptEntry::PeerReview {
                    parsed_ranking,
                    success,
                    ..
                } => Some((success, parsed_ranking.clone())),
                _ => None,
            })
            .collect();
        assert_eq!(
            reviews,
            vec![(
                &true,
                vec!["Response B".to_string(), "Response A".to_string()]
            )]
        );
        let rankings = app
            .transcript
            .iter()
            .find_map(|e| match e {
                TranscriptEntry::Rankings { entries } => Some(entries.clone()),
                _ => None,
            })
            .expect("rankings entry");
        assert_eq!(rankings[0].name, "b/free");
        assert_eq!(
            ranking_outcome(&rankings),
            RankingOutcome::Winner(rankings[0].clone())
        );
    }

    #[test]
    fn ranking_outcome_requires_a_unique_best() {
        let entry = |id: &str, avg: f64| RankEntry {
            model_id: id.into(),
            name: id.into(),
            average_rank: avg,
            rankings_count: 1,
        };
        assert_eq!(ranking_outcome(&[]), RankingOutcome::None);
        assert_eq!(
            ranking_outcome(&[entry("a", 1.0), entry("b", 2.0)]),
            RankingOutcome::Winner(entry("a", 1.0))
        );
        let tied = ranking_outcome(&[entry("a", 1.5), entry("b", 1.5), entry("c", 3.0)]);
        match tied {
            RankingOutcome::Tie(mut leaders) => {
                leaders.sort_by(|l, r| l.model_id.cmp(&r.model_id));
                assert_eq!(leaders, vec![entry("a", 1.5), entry("b", 1.5)]);
            }
            other => panic!("expected tie, got {other:?}"),
        }
    }

    #[test]
    fn final_without_answer_surfaces_protocol_error() {
        let mut app = app();
        app.session = Some(session("s"));
        app.loading = true;
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({"type": "final", "payload": {"metadata": {}}}),
        });
        assert!(!app.loading);
        match app.transcript.back() {
            Some(TranscriptEntry::Notice { text }) => {
                assert!(text.contains("neither an answer nor an error"));
            }
            other => panic!("expected protocol notice, got {other:?}"),
        }
    }

    #[test]
    fn final_uses_metadata_rankings_when_rankings_event_is_missing() {
        let mut app = app();
        app.session = Some(session("s"));
        app.selected_models = vec!["a/free".into()];
        app.begin_run("q".into());
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({
                "type": "final",
                "payload": {
                    "final_answer": "done",
                    "metadata": {
                        "final_answer_model_id": "a/free",
                        "aggregate_rankings": [
                            {"model": "a/free", "average_rank": 1.0, "rankings_count": 1}
                        ],
                    },
                },
            }),
        });
        let kinds: Vec<&str> = app
            .transcript
            .iter()
            .map(|e| match e {
                TranscriptEntry::User { .. } => "user",
                TranscriptEntry::ModelResult { .. } => "model",
                TranscriptEntry::PeerReview { .. } => "review",
                TranscriptEntry::Rankings { .. } => "rankings",
                TranscriptEntry::Synthesis { .. } => "synthesis",
                TranscriptEntry::Source { .. } => "source",
                TranscriptEntry::Notice { .. } => "notice",
            })
            .collect();
        let rankings_at = kinds.iter().position(|k| *k == "rankings").unwrap();
        let synthesis_at = kinds.iter().position(|k| *k == "synthesis").unwrap();
        assert!(rankings_at < synthesis_at);
    }

    #[test]
    fn done_without_final_settles_and_reports_protocol_error() {
        let mut app = app();
        app.session = Some(session("s"));
        app.selected_models = vec!["a/free".into()];
        app.begin_run("q".into());
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({"type": "stage1_start", "models": ["a/free"]}),
        });
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({"type": "done"}),
        });
        assert!(!app.loading);
        assert_eq!(
            model_states(&app),
            vec![("a/free".to_string(), ModelStatus::Failed)]
        );
        assert!(matches!(
            app.transcript.back(),
            Some(TranscriptEntry::Notice { text }) if text.contains("protocol error")
        ));
    }

    #[test]
    fn stream_end_overrides_a_missing_done_and_fails_active_models() {
        let mut app = app();
        app.session = Some(session("s"));
        app.selected_models = vec!["a/free".into()];
        app.begin_run("q".into());
        app.handle(ApiEvent::StreamEnded {
            session: "s".into(),
            malformed: 1,
        });
        assert!(!app.loading);
        assert_eq!(
            model_states(&app),
            vec![("a/free".to_string(), ModelStatus::Failed)]
        );
        let notices: Vec<&str> = app
            .transcript
            .iter()
            .filter_map(|e| match e {
                TranscriptEntry::Notice { text } => Some(text.as_str()),
                _ => None,
            })
            .collect();
        assert!(notices.iter().any(|n| n.contains("malformed stream event")));
        assert!(notices.iter().any(|n| n.contains("protocol error")));
    }

    #[test]
    fn stream_end_for_another_session_is_ignored() {
        let mut app = app();
        app.session = Some(session("s"));
        app.loading = true;
        app.handle(ApiEvent::StreamEnded {
            session: "other".into(),
            malformed: 0,
        });
        assert!(app.loading);
        assert!(app.transcript.is_empty());
    }

    #[test]
    fn search_failure_metadata_is_reported_honestly() {
        let mut app = app();
        app.session = Some(session("s"));
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({
                "type": "session",
                "conversation_id": "s",
                "web_search": {
                    "enabled": true,
                    "status": "error",
                    "error_message": "blocked by network",
                },
            }),
        });
        match app.transcript.back() {
            Some(TranscriptEntry::Notice { text }) => {
                assert!(text.contains("Web search failed"));
                assert!(text.contains("blocked by network"));
            }
            other => panic!("expected search notice, got {other:?}"),
        }
    }

    #[test]
    fn disabled_search_adds_no_source_notice() {
        let mut app = app();
        app.session = Some(session("s"));
        app.handle(ApiEvent::Stream {
            session: "s".into(),
            event: serde_json::json!({
                "type": "final",
                "payload": {
                    "final_answer": "answer",
                    "metadata": {"search": {"enabled": false}},
                    "sources": [],
                },
            }),
        });
        assert!(!app
            .transcript
            .iter()
            .any(|e| matches!(e, TranscriptEntry::Notice { text } if text.to_lowercase().contains("source"))));
    }

    #[test]
    fn transport_error_mid_run_settles_cards_and_shows_the_error() {
        let mut app = app();
        app.session = Some(session("s"));
        app.selected_models = vec!["a/free".into()];
        app.begin_run("q".into());
        app.handle(ApiEvent::Error(
            "stream interrupted: connection reset".into(),
        ));
        assert!(!app.loading);
        assert_eq!(
            model_states(&app),
            vec![("a/free".to_string(), ModelStatus::Failed)]
        );
        assert!(matches!(
            app.transcript.back(),
            Some(TranscriptEntry::Notice { text }) if text.contains("connection reset")
        ));
        assert!(app.status.contains("connection reset"));
    }
}
