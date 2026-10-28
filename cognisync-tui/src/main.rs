mod api;
mod app;
mod config;

use anyhow::Result;
use app::{App, Overlay, Screen, TranscriptEntry, COMMANDS};
use clap::Parser;
use config::Config;
use crossterm::event::{
    Event as TermEvent, EventStream, KeyCode, KeyEvent, KeyEventKind, KeyModifiers,
};
use futures_util::StreamExt;
use ratatui::{
    layout::{Constraint, Layout, Rect},
    style::{Color, Modifier, Style, Stylize},
    text::Line,
    widgets::{Block, Clear, List, ListItem, ListState, Paragraph, Wrap},
    Frame,
};
use tokio::sync::mpsc::{unbounded_channel, UnboundedReceiver};

/// Per-session interaction state that lives outside the domain `App`.
struct UiState {
    backend_url: String,
    login: LoginState,
}
struct LoginState {
    username: String,
    password: String,
    field: usize,
    register: bool,
}
impl LoginState {
    // ponytail: keep previously typed username on /login (no reset call site)
}

#[derive(Parser)]
#[command(
    name = "cognisync-tui",
    about = "Keyboard-first terminal client for the CogniSync API"
)]
struct Args {
    /// Backend base URL (also reads COGNISYNC_BACKEND_URL).
    #[arg(long, env = "COGNISYNC_BACKEND_URL")]
    backend_url: Option<String>,
}

#[tokio::main]
async fn main() -> Result<()> {
    let args = Args::parse();
    let cfg = Config::load(args.backend_url.clone())?;
    let api = api::Api::new(cfg.backend_url.clone())?;
    let (tx, rx) = unbounded_channel();
    let app = App::new(tx);
    app.fetch_bootstrap(api.clone());
    run(app, api, rx, cfg).await
}

async fn run(
    mut app: App,
    mut api: api::Api,
    mut rx: UnboundedReceiver<api::ApiEvent>,
    cfg: Config,
) -> Result<()> {
    let mut terminal = ratatui::init();
    let mut state = UiState {
        backend_url: cfg.backend_url.clone(),
        login: LoginState {
            username: String::new(),
            password: String::new(),
            field: 0,
            register: false,
        },
    };
    let mut events = EventStream::new();
    let mut ticker = tokio::time::interval(std::time::Duration::from_millis(200));
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let outcome = loop {
        terminal.draw(|f| ui(f, &app, &state))?;
        if app.quit {
            break Ok(());
        }
        tokio::select! {
            Some(ev) = events.next() => {
                if let TermEvent::Key(key) = ev? {
                    if key.kind == KeyEventKind::Press {
                        handle_key(&mut app, &api, &mut state, key);
                        // `/logout` clears the in-memory token for real: a
                        // stale bearer must not keep authorizing requests.
                        if !app.is_authenticated {
                            api.set_token(String::new());
                        }
                        if app.quit {
                            break Ok(());
                        }
                    }
                }
            }
            Some(ev) = rx.recv() => {
                if let api::ApiEvent::Login(Ok(auth)) = &ev {
                    api.set_token(auth.access_token.clone());
                }
                let signed_in = matches!(&ev, api::ApiEvent::Login(Ok(_)));
                app.handle(ev);
                if signed_in {
                    refresh_history(&app, api.clone());
                }
            }
            // Animate only while a run is active; idle screens stay quiet.
            _ = ticker.tick(), if app.loading => app.tick(),
        }
    };
    ratatui::restore();
    outcome
}

/// Pull the user's saved conversations right after a successful sign-in.
fn refresh_history(app: &App, api: api::Api) {
    let tx = app.tx.clone();
    tokio::spawn(async move {
        let r = match api.history().await {
            Ok(x) => api::ApiEvent::History(Ok(x)),
            Err(e) => api::ApiEvent::History(Err(e.to_string())),
        };
        let _ = tx.send(r);
    });
}

fn handle_key(app: &mut App, api: &api::Api, state: &mut UiState, key: KeyEvent) {
    if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
        app.quit = true;
        return;
    }
    if app.overlay == Overlay::Palette {
        palette_key(app, api, state, key);
        return;
    }
    match app.screen {
        Screen::Login => login_key(app, api, state, key),
        Screen::Chat => chat_key(app, api, key),
        Screen::History => history_key(app, api, key),
        Screen::Models => models_key(app, key),
        Screen::Welcome | Screen::Help | Screen::Settings | Screen::Tools => {
            general_key(app, api, key)
        }
    }
}

/// Keys that work on every non-dialog screen: shortcuts and the command palette.
fn general_key(app: &mut App, api: &api::Api, key: KeyEvent) {
    match key.code {
        KeyCode::Char('n') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            new_session(app, api)
        }
        KeyCode::Char('h') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            open_history(app, api)
        }
        KeyCode::Char('m') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            open_models(app, api)
        }
        KeyCode::Char('t') if key.modifiers.contains(KeyModifiers::CONTROL) => open_tools(app, api),
        KeyCode::Char('/') | KeyCode::Char(':') => open_palette(app, String::new()),
        KeyCode::Enter if app.screen == Screen::Welcome && app.is_authenticated => {
            new_session(app, api)
        }
        KeyCode::Esc => {
            app.screen = if app.session.is_some() {
                Screen::Chat
            } else {
                Screen::Welcome
            }
        }
        _ => {}
    }
}

fn open_palette(app: &mut App, seed: String) {
    app.overlay = Overlay::Palette;
    app.palette = seed;
    app.selected = 0;
}

fn palette_key(app: &mut App, api: &api::Api, _state: &mut UiState, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => app.overlay = Overlay::None,
        KeyCode::Up => app.selected = app.selected.saturating_sub(1),
        KeyCode::Down => {
            let len = app.command_matches().len();
            if app.selected + 1 < len {
                app.selected += 1;
            }
        }
        KeyCode::Backspace => {
            app.palette.pop();
        }
        KeyCode::Char(c) => app.palette.push(c),
        KeyCode::Enter => {
            let matches = app.command_matches();
            if let Some((cmd, _)) = matches.get(app.selected.min(matches.len().saturating_sub(1))) {
                let cmd = cmd.to_string();
                app.overlay = Overlay::None;
                app.palette.clear();
                execute_command(app, api, &cmd);
            } else if app.palette.starts_with('/') {
                // Free-form command entry, e.g. a partially typed known command.
                let cmd = app.palette.trim().to_string();
                app.overlay = Overlay::None;
                app.palette.clear();
                execute_command(app, api, &cmd);
            }
        }
        _ => {}
    }
}

fn execute_command(app: &mut App, api: &api::Api, cmd: &str) {
    let cmd = cmd.trim();
    let body = cmd.strip_prefix('/').unwrap_or(cmd);
    match body {
        "new" => new_session(app, api),
        "history" => open_history(app, api),
        "models" => open_models(app, api),
        "search" => app.toggle_web_search(),
        "compare" => app.status = "Model responses are shown inline in the transcript".into(),
        "mcps" => open_tools(app, api),
        "settings" => app.screen = Screen::Settings,
        "help" => app.screen = Screen::Help,
        "login" => {
            app.screen = Screen::Login;
            app.selected = 0;
        }
        "register" => {
            app.screen = Screen::Login;
            app.selected = 0;
        }
        "logout" => {
            app.is_authenticated = false;
            app.access_token.clear();
            app.username.clear();
            app.screen = Screen::Welcome;
            app.status = "Signed out".into();
        }
        "exit" => app.quit = true,
        other => app.status = format!("Unknown command '/{other}'; try /help"),
    }
}

fn new_session(app: &mut App, api: &api::Api) {
    if !app.is_authenticated {
        app.screen = Screen::Login;
        app.status = "Sign in to start a session".into();
        return;
    }
    app.status = "Creating session…".into();
    let tx = app.tx.clone();
    let api = api.clone();
    tokio::spawn(async move {
        let r = api.create().await;
        let _ = tx.send(api::ApiEvent::Created(r.map_err(|e| e.to_string())));
    });
}

fn open_history(app: &mut App, api: &api::Api) {
    if !app.is_authenticated {
        app.screen = Screen::Login;
        app.status = "Sign in to browse history".into();
        return;
    }
    app.screen = Screen::History;
    app.selected = 0;
    app.status = "Loading history…".into();
    let tx = app.tx.clone();
    let api = api.clone();
    tokio::spawn(async move {
        let r = api.history().await;
        let _ = tx.send(api::ApiEvent::History(r.map_err(|e| e.to_string())));
    });
}

fn open_models(app: &mut App, api: &api::Api) {
    if !app.is_authenticated {
        app.screen = Screen::Login;
        app.status = "Sign in to load the model catalog".into();
        return;
    }
    app.begin_model_picker();
    let tx = app.tx.clone();
    let api = api.clone();
    tokio::spawn(async move {
        let r = api.models().await;
        let _ = tx.send(api::ApiEvent::Models(r.map_err(|e| e.to_string())));
    });
}

fn open_tools(app: &mut App, api: &api::Api) {
    if !app.is_authenticated {
        app.screen = Screen::Login;
        app.status = "Sign in to inspect tools".into();
        return;
    }
    app.screen = Screen::Tools;
    app.status = "Loading tool status…".into();
    let tx = app.tx.clone();
    let api = api.clone();
    tokio::spawn(async move {
        let r = api.tools().await;
        let _ = tx.send(api::ApiEvent::Tools(r.map_err(|e| e.to_string())));
    });
}

fn chat_key(app: &mut App, api: &api::Api, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => {
            if app.input.is_empty() && app.session.is_some() {
                app.screen = Screen::Welcome;
            } else {
                app.input.clear();
            }
        }
        KeyCode::Backspace => {
            app.input.pop();
        }
        KeyCode::Char('/') => open_palette(app, "/".into()),
        KeyCode::Char('n') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            new_session(app, api)
        }
        KeyCode::Char('h') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            open_history(app, api)
        }
        KeyCode::Char('m') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            open_models(app, api)
        }
        KeyCode::Char('t') if key.modifiers.contains(KeyModifiers::CONTROL) => open_tools(app, api),
        KeyCode::Enter => send_message(app, api),
        KeyCode::Char(c) => app.input.push(c),
        _ => {}
    }
}

fn send_message(app: &mut App, api: &api::Api) {
    if app.input.is_empty() {
        return;
    }
    if app.loading {
        app.status = "Council run already in progress".into();
        return;
    }
    if !app.is_authenticated {
        app.screen = Screen::Login;
        app.status = "Sign in first".into();
        return;
    }
    if app.selected_models.is_empty() {
        app.status = "Select council models first: /models".into();
        return;
    }
    let Some(session) = app.session.as_ref().map(|s| s.id.clone()) else {
        app.status = "Create a session first: /new".into();
        return;
    };
    let content = std::mem::take(&mut app.input);
    app.begin_run(content.clone());
    let tx = app.tx.clone();
    let api = api.clone();
    let models = app.selected_models.clone();
    let web_search = app.web_search;
    tokio::spawn(async move {
        if let Err(e) = api
            .stream_message(session, content, models, web_search, tx.clone())
            .await
        {
            let _ = tx.send(api::ApiEvent::Error(e.to_string()));
        }
    });
}

fn history_key(app: &mut App, api: &api::Api, key: KeyEvent) {
    match key.code {
        KeyCode::Esc | KeyCode::Char('q') => {
            app.screen = if app.session.is_some() {
                Screen::Chat
            } else {
                Screen::Welcome
            }
        }
        KeyCode::Up => app.selected = app.selected.saturating_sub(1),
        KeyCode::Down => {
            if app.selected + 1 < app.sessions.len() {
                app.selected += 1;
            }
        }
        KeyCode::Char('n') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            new_session(app, api)
        }
        KeyCode::Enter => {
            if let Some(session) = app.sessions.get(app.selected) {
                let id = session.id.clone();
                app.status = "Opening conversation…".into();
                let tx = app.tx.clone();
                let api = api.clone();
                tokio::spawn(async move {
                    let r = api.open(&id).await;
                    let _ = tx.send(api::ApiEvent::Open(r.map_err(|e| e.to_string())));
                });
            }
        }
        KeyCode::Char('d') => {
            if let Some(session) = app.sessions.get(app.selected) {
                let id = session.id.clone();
                let title = session.title.clone();
                let tx = app.tx.clone();
                let api = api.clone();
                tokio::spawn(async move {
                    match api.delete_conversation(&id).await {
                        Ok(()) => {
                            let r = api.history().await;
                            let _ = tx.send(api::ApiEvent::History(r.map_err(|e| e.to_string())));
                        }
                        Err(e) => {
                            let _ = tx.send(api::ApiEvent::Error(e.to_string()));
                        }
                    }
                });
                app.status = format!("Deleted {title}");
            }
        }
        _ => {}
    }
}

fn models_key(app: &mut App, key: KeyEvent) {
    match key.code {
        KeyCode::Esc => app.cancel_model_picker(),
        KeyCode::Up => app.selected = app.selected.saturating_sub(1),
        KeyCode::Down => {
            if app.selected + 1 < app.models.len() {
                app.selected += 1;
            }
        }
        KeyCode::Char(' ') => app.toggle_draft_model(),
        KeyCode::Enter => {
            app.confirm_model_picker();
        }
        _ => {}
    }
}

fn login_key(app: &mut App, api: &api::Api, state: &mut UiState, key: KeyEvent) {
    let login = &mut state.login;
    match key.code {
        KeyCode::Esc => {
            app.screen = Screen::Welcome;
            app.status = "Login cancelled".into();
        }
        KeyCode::Down | KeyCode::Tab => login.field = 1,
        KeyCode::Up => login.field = 0,
        KeyCode::F(2) => login.register = !login.register,
        KeyCode::Enter => {
            if login.username.trim().is_empty() || login.password.is_empty() {
                app.status = "Enter both a username and password".into();
                return;
            }
            let username = login.username.trim().to_string();
            let password = login.password.clone();
            let register = login.register;
            app.status = if register {
                "Creating account…".into()
            } else {
                "Signing in…".into()
            };
            app.screen = Screen::Welcome;
            let tx = app.tx.clone();
            let api = api.clone();
            tokio::spawn(async move {
                let r = if register {
                    api.register(username, password).await
                } else {
                    api.login(username, password).await
                };
                let _ = tx.send(api::ApiEvent::Login(r.map_err(|e| e.to_string())));
            });
        }
        KeyCode::Backspace => match login.field {
            0 => {
                login.username.pop();
            }
            _ => {
                login.password.pop();
            }
        },
        KeyCode::Char(c) => match login.field {
            0 => login.username.push(c),
            _ => login.password.push(c),
        },
        _ => {}
    }
}

// ---------------------------------------------------------------- rendering

fn palette(f: &mut Frame, app: &App, area: Rect) {
    let matches = app.command_matches();
    let height = matches.len() as u16 + 3; // palette line, spacing, borders
    let width = area.width.min(80);
    let x = (area.width.saturating_sub(width)) / 2;
    let y = area.height.saturating_sub(height + 1); // sits above the status line
    let rect = Rect {
        x: area.x + x,
        y: area.y + y,
        width,
        height: height.min(area.height.saturating_sub(area.y + y)),
    };
    let mut lines = vec![
        Line::styled(format!("> {}", app.palette), Style::new().bold()),
        Line::default(),
    ];
    for (i, (cmd, desc)) in matches.iter().enumerate() {
        let style = if i == app.selected {
            Style::new().bg(Color::Cyan).fg(Color::Black)
        } else {
            Style::new()
        };
        lines.push(Line::styled(format!("{cmd:<10} {desc}"), style));
    }
    f.render_widget(Clear, rect);
    f.render_widget(
        Paragraph::new(lines)
            .block(Block::bordered().title("Commands — ↑/↓ select · Enter run · Esc close")),
        rect,
    );
}

fn ui(f: &mut Frame, app: &App, state: &UiState) {
    let area = f.area();
    let [main, status] = Layout::vertical([Constraint::Min(1), Constraint::Length(1)]).areas(area);
    match app.screen {
        Screen::Welcome => welcome(f, app, main),
        Screen::Login => login(f, app, state, main),
        Screen::Chat => chat(f, app, main),
        Screen::History => history(f, app, main),
        Screen::Models => models(f, app, main),
        Screen::Tools => tools(f, app, main),
        Screen::Settings => settings(f, app, state, main),
        Screen::Help => help(f, app, main),
    }
    let connection = if app.connected { "● " } else { "○ " };
    let status_text = if app.loading {
        format!("{connection}{} {}", spinner(app), clean(&app.status))
    } else {
        format!("{connection}{}", clean(&app.status))
    };
    f.render_widget(
        Paragraph::new(status_text).style(Style::new().dim()),
        status,
    );
    if app.overlay == Overlay::Palette {
        palette(f, app, area);
    }
}

fn screen_block(title: &str) -> Block<'static> {
    Block::bordered().title(title.to_string())
}

const SPINNER: [&str; 10] = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

fn spinner(app: &App) -> &'static str {
    SPINNER[app.thinking_dots % SPINNER.len()]
}

/// Strip control characters from untrusted backend text (model output,
/// search titles/URLs, errors) so it cannot drive terminal escape sequences.
fn clean(text: &str) -> String {
    text.chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect()
}

fn welcome(f: &mut Frame, app: &App, area: Rect) {
    let auth = if app.is_authenticated {
        format!("Signed in as {}.", app.username)
    } else {
        "Not signed in — run /login (or /register to create an account).".into()
    };
    let models_select = if app.selected_models.is_empty() {
        "No council models selected yet — Ctrl+M to pick."
    } else {
        "Council models selected — press Enter to start a session."
    };
    let lines = vec![
        Line::styled("LLM Council", Style::new().bold()),
        Line::default(),
        Line::from(auth),
        Line::default(),
        Line::from("Ctrl+N new session   Ctrl+H history   Ctrl+M models   Ctrl+T tools"),
        Line::from("/ command palette · /help all commands · Ctrl+C exit"),
        Line::default(),
        Line::from(models_select),
    ];
    f.render_widget(Paragraph::new(lines).block(screen_block("Cognisync")), area);
}

fn help(f: &mut Frame, _app: &App, area: Rect) {
    let mut items: Vec<ListItem> = vec![
        ListItem::new("Chat: Enter send · Esc clear · Ctrl+H history · Ctrl+M models · Ctrl+T tools · Ctrl+C quit"),
        ListItem::new(""),
    ];
    items.extend(
        COMMANDS
            .iter()
            .map(|(cmd, desc)| ListItem::new(format!("{cmd:<10} {desc}"))),
    );
    f.render_stateful_widget(
        List::new(items)
            .block(screen_block("Commands — Esc to return"))
            .highlight_symbol("▶ "),
        area,
        &mut ListState::default(),
    );
}

fn history(f: &mut Frame, app: &App, area: Rect) {
    if app.sessions.is_empty() {
        f.render_widget(
            Paragraph::new("No saved conversations. Ctrl+N to start one.")
                .block(screen_block("History — Esc to return")),
            area,
        );
        return;
    }
    let items: Vec<ListItem> = app
        .sessions
        .iter()
        .map(|s| {
            ListItem::new(format!(
                "{} · {} messages · {}",
                s.title, s.message_count, s.updated_at
            ))
        })
        .collect();
    let mut list_state = ListState::default();
    list_state.select(Some(app.selected.min(app.sessions.len() - 1)));
    f.render_stateful_widget(
        List::new(items)
            .block(screen_block(
                "History — Enter open · d delete · Ctrl+N new · Esc back",
            ))
            .highlight_style(Style::new().bg(Color::Cyan).fg(Color::Black)),
        area,
        &mut list_state,
    );
}

fn models(f: &mut Frame, app: &App, area: Rect) {
    let items: Vec<ListItem> = app
        .models
        .iter()
        .map(|m| {
            let checked = if app.draft_models.contains(&m.id) {
                "[x]"
            } else {
                "[ ]"
            };
            ListItem::new(format!(
                "{checked} {}{}",
                m.name,
                if m.free { " · free" } else { "" }
            ))
        })
        .collect();
    let mut list_state = ListState::default();
    list_state.select(Some(app.selected.min(app.models.len().saturating_sub(1))));
    let footer = format!(
        "draft {} selected (min {} / max {})",
        app.draft_models.len(),
        app.min_selection,
        app.max_selection
    );
    f.render_stateful_widget(
        List::new(items)
            .block(screen_block(&format!(
                "Models — Space toggle · Enter confirm · Esc cancel · {footer}"
            )))
            .highlight_style(Style::new().bg(Color::Cyan).fg(Color::Black)),
        area,
        &mut list_state,
    );
}

fn tools(f: &mut Frame, app: &App, area: Rect) {
    let lines = match &app.tools {
        None => vec![Line::from("Loading tool status…")],
        Some(t) => vec![
            Line::styled(
                format!("authentication: {}", t.authentication),
                Style::new().bold(),
            ),
            Line::default(),
            Line::styled(
                clean(
                    &serde_json::to_string_pretty(&t.integrations)
                        .unwrap_or_else(|_| "(unavailable)".into()),
                ),
                Style::new().dim(),
            ),
        ],
    };
    f.render_widget(
        Paragraph::new(lines).block(screen_block("Backend tools — Esc to return")),
        area,
    );
}

fn settings(f: &mut Frame, app: &App, state: &UiState, area: Rect) {
    let (health_line, council) = match &app.health {
        Some(h) => (
            format!("service: {} · status: {}", h.service, h.status),
            format!(
                "chairman: {} · council: {}",
                h.chairman_model,
                h.council_models.join(", ")
            ),
        ),
        None => ("health not loaded".into(), String::new()),
    };
    let auth = if app.is_authenticated {
        format!("signed in as {}", app.username)
    } else {
        "not signed in".into()
    };
    let lines = vec![
        Line::styled(
            format!("backend: {}", state.backend_url),
            Style::new().bold(),
        ),
        Line::from(health_line),
        Line::from(council),
        Line::from(auth),
        Line::default(),
        Line::from(format!(
            "web search grounding: {}",
            if app.web_search { "on" } else { "off" }
        )),
        Line::from(format!(
            "selected models: {}",
            app.selected_models.join(", ")
        )),
    ];
    f.render_widget(
        Paragraph::new(lines).block(screen_block("Settings — Esc to return")),
        area,
    );
}

fn login(f: &mut Frame, _app: &App, state: &UiState, area: Rect) {
    let rect = centered_rect(60, 34, area);
    let login = &state.login;
    let (title, hint) = if login.register {
        ("Create account (F2 switch to sign in)", "register")
    } else {
        ("Sign in (F2 switch to register)", "login")
    };
    let password_display = "•".repeat(login.password.chars().count());
    let marker = |field: usize| if login.field == field { "▶" } else { " " };
    let lines = vec![
        Line::from(format!("{} Username: {}", marker(0), login.username)),
        Line::from(format!("{} Password: {}", marker(1), password_display)),
        Line::default(),
        Line::styled(
            format!("Enter submits · Up/Down/Tab switch field · Esc cancels · {hint}"),
            Style::new().dim(),
        ),
    ];
    f.render_widget(Clear, rect);
    f.render_widget(
        Paragraph::new(lines).block(Block::bordered().title(title)),
        rect,
    );
}

fn centered_rect(pct_x: u16, pct_y: u16, area: Rect) -> Rect {
    let vertical = Layout::vertical([
        Constraint::Percentage((100 - pct_y) / 2),
        Constraint::Percentage(pct_y),
        Constraint::Percentage((100 - pct_y + 1) / 2),
    ])
    .split(area);
    let horizontal = Layout::horizontal([
        Constraint::Percentage((100 - pct_x) / 2),
        Constraint::Percentage(pct_x),
        Constraint::Percentage((100 - pct_x + 1) / 2),
    ])
    .split(vertical[1]);
    horizontal[1]
}

fn chat(f: &mut Frame, app: &App, area: Rect) {
    let [transcript_area, input_area] =
        Layout::vertical([Constraint::Min(3), Constraint::Length(3)]).areas(area);
    let lines = transcript_lines(app);
    let view_rows = transcript_area.height.saturating_sub(2) as usize; // borders
    let max_scroll = lines.len().saturating_sub(view_rows);
    let scroll = app.conversation_scroll.min(max_scroll); // usize::MAX = follow bottom
    f.render_widget(
        Paragraph::new(lines)
            .block(screen_block(
                "Council — Enter send · Esc clear · Ctrl+H history · Ctrl+M models · / commands",
            ))
            .wrap(Wrap { trim: false })
            .scroll((scroll as u16, 0)),
        transcript_area,
    );
    f.render_widget(
        Paragraph::new(app.input.as_str()).block(
            Block::bordered()
                .title("Prompt — Enter send · Esc clear · Ctrl+H/M/T · /help for all keys"),
        ),
        input_area,
    );
}

fn transcript_lines(app: &App) -> Vec<Line<'static>> {
    let mut lines = Vec::new();
    for entry in &app.transcript {
        match entry {
            TranscriptEntry::User { text } => {
                lines.push(Line::styled(
                    format!("you ▸ {}", clean(text)),
                    Style::new().fg(Color::Cyan),
                ));
                lines.push(Line::default());
            }
            TranscriptEntry::ModelResult {
                model,
                name,
                status,
                text,
                tokens,
                duration_ms,
                ..
            } => {
                let label = clean(&name.clone().unwrap_or_else(|| model.clone()));
                let (marker, color, state) = match status {
                    app::ModelStatus::Waiting => ("○", Color::DarkGray, " · waiting"),
                    app::ModelStatus::Thinking => (spinner(app), Color::Yellow, " · thinking…"),
                    app::ModelStatus::Complete => ("✓", Color::Green, ""),
                    app::ModelStatus::Failed => ("✗", Color::Red, " · failed"),
                };
                let mut header = format!("{marker} {label}{state}");
                if matches!(status, app::ModelStatus::Complete) {
                    if let Some(tokens) = tokens {
                        header.push_str(&format!(" · {tokens} tok"));
                    }
                    if let Some(ms) = duration_ms {
                        header.push_str(&format!(" · {ms} ms"));
                    }
                }
                lines.push(Line::styled(
                    header,
                    Style::new().fg(color).add_modifier(Modifier::BOLD),
                ));
                if !status.is_active() {
                    for l in text.lines() {
                        lines.push(Line::from(clean(l)));
                    }
                }
                lines.push(Line::default());
            }
            TranscriptEntry::PeerReview {
                model_id,
                name,
                success,
                error,
                parsed_ranking,
            } => {
                let label = clean(&name.clone().unwrap_or_else(|| model_id.clone()));
                if *success {
                    let ranking = if parsed_ranking.is_empty() {
                        "no parsed ranking".to_string()
                    } else {
                        clean(&parsed_ranking.join(" > "))
                    };
                    lines.push(Line::styled(
                        format!("◆ peer review · {label} · {ranking}"),
                        Style::new().fg(Color::Blue),
                    ));
                } else {
                    let err = clean(error.as_deref().unwrap_or("review failed"));
                    lines.push(Line::styled(
                        format!("✗ peer review · {label} · {err}"),
                        Style::new().fg(Color::Red),
                    ));
                }
            }
            TranscriptEntry::Rankings { entries } => {
                lines.push(Line::styled(
                    "✧ aggregate rankings (lower average is better)",
                    Style::new().fg(Color::Magenta).add_modifier(Modifier::BOLD),
                ));
                for (i, rank) in entries.iter().enumerate() {
                    lines.push(Line::from(format!(
                        "  {}. {} — avg {:.2} · {} vote{}",
                        i + 1,
                        clean(&rank.name),
                        rank.average_rank,
                        rank.rankings_count,
                        if rank.rankings_count == 1 { "" } else { "s" }
                    )));
                }
                match app::ranking_outcome(entries) {
                    app::RankingOutcome::Winner(winner) => lines.push(Line::styled(
                        format!("  winner: {}", clean(&winner.name)),
                        Style::new().fg(Color::Green),
                    )),
                    app::RankingOutcome::Tie(leaders) => lines.push(Line::styled(
                        format!(
                            "  tied at {:.2}: {}",
                            leaders[0].average_rank,
                            leaders
                                .iter()
                                .map(|l| clean(&l.name))
                                .collect::<Vec<_>>()
                                .join(", ")
                        ),
                        Style::new().fg(Color::Yellow),
                    )),
                    app::RankingOutcome::None => {}
                }
                lines.push(Line::default());
            }
            TranscriptEntry::Synthesis { text, model, error } => {
                if let Some(e) = error {
                    lines.push(Line::styled(
                        format!(
                            "✗ synthesis failed{of}: {e}",
                            of = model
                                .as_deref()
                                .map(|m| format!(" ({})", clean(m)))
                                .unwrap_or_default(),
                            e = clean(e)
                        ),
                        Style::new().fg(Color::Red),
                    ));
                } else {
                    let of = model
                        .as_deref()
                        .map(|m| format!(" ({})", clean(m)))
                        .unwrap_or_default();
                    lines.push(Line::styled(
                        format!("✦ final synthesis{of}"),
                        Style::new().fg(Color::Green).add_modifier(Modifier::BOLD),
                    ));
                    for l in text.lines() {
                        lines.push(Line::from(clean(l)));
                    }
                }
                lines.push(Line::default());
            }
            TranscriptEntry::Source { title, url } => {
                lines.push(Line::styled(
                    format!("· {} — {}", clean(title), clean(url)),
                    Style::new().fg(Color::Yellow),
                ));
            }
            TranscriptEntry::Notice { text } => {
                lines.push(Line::styled(
                    format!("! {}", clean(text)),
                    Style::new().fg(Color::Yellow).add_modifier(Modifier::BOLD),
                ));
                lines.push(Line::default());
            }
        }
    }
    if app.loading {
        lines.push(Line::styled(
            format!("{} council is working…", spinner(app)),
            Style::new().dim(),
        ));
    }
    lines
}
