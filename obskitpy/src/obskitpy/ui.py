"""Terminal bonito, sem dependências: cores, spinner, caixas, perguntas e links.

Respeita NO_COLOR, FORCE_COLOR e a saída não interativa (pipes/CI): nesse caso imprime texto simples.
"""
import os
import re
import shutil
import sys
import threading
import time

_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\]8;;[^\x1b]*\x1b\\")


def tty() -> bool:
    return sys.stdout.isatty()


def interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def color_on() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    return tty() and os.environ.get("TERM") != "dumb"


def _truecolor() -> bool:
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


# nome → (truecolor RGB, código 256 cores)
PALETTE = {
    "teal": ((25, 211, 182), 43),
    "amber": ((255, 184, 28), 214),
    "green": ((91, 214, 138), 78),
    "red": ((255, 123, 123), 203),
    "blue": ((110, 168, 255), 111),
    "gray": ((102, 118, 154), 244),
    "white": ((230, 237, 248), 255),
}


def _fg(name: str) -> str:
    rgb, code = PALETTE[name]
    return f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m" if _truecolor() else f"\x1b[38;5;{code}m"


def paint(text: str, *styles: str) -> str:
    """Aplica estilos (bold, dim, teal, amber, green, red, blue, gray, white) se o terminal suportar cor."""
    if not color_on() or not styles:
        return text
    codes = "".join({"bold": "\x1b[1m", "dim": "\x1b[2m", "italic": "\x1b[3m"}.get(s) or _fg(s) for s in styles)
    return f"{codes}{text}\x1b[0m"


def visible_len(text: str) -> int:
    return len(_ANSI.sub("", text))


def term_width() -> int:
    return max(40, min(shutil.get_terminal_size((100, 24)).columns, 110))


def link(url: str, label: str | None = None) -> str:
    text = paint(label or url, "teal", "bold")
    if color_on():
        return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"
    return label or url


def _gradient(text: str, a: str = "teal", b: str = "amber") -> str:
    if not color_on():
        return text
    (r1, g1, b1), (r2, g2, b2) = PALETTE[a][0], PALETTE[b][0]
    out, n = "", max(len(text) - 1, 1)
    for i, ch in enumerate(text):
        t = i / n
        rgb = (int(r1 + (r2 - r1) * t), int(g1 + (g2 - g1) * t), int(b1 + (b2 - b1) * t))
        out += (f"\x1b[38;2;{rgb[0]};{rgb[1]};{rgb[2]}m{ch}" if _truecolor()
                else f"{_fg('teal') if t < .5 else _fg('amber')}{ch}")
    return out + "\x1b[0m"


def banner(version: str, subtitle: str = "observabilidade para automações Python") -> None:
    pulse = _gradient("▁▂▁▁▃▇▂▁▁▂▁")
    print()
    print(f"  {paint('obskit', 'bold', 'white')}{paint('py', 'bold', 'teal')}  {pulse}  {paint('v' + version, 'gray')}")
    print(f"  {paint(subtitle, 'gray')}")
    print()


def out(text: str = "") -> None:
    print(text)


def heading(text: str) -> None:
    print(f"\n{paint('▍', 'teal')}{paint(text, 'bold', 'white')}")


def ok(text: str) -> None:
    print(f"{paint('✔', 'green', 'bold')} {text}")


def warn(text: str) -> None:
    print(f"{paint('!', 'amber', 'bold')} {text}")


def fail(text: str) -> None:
    print(f"{paint('✖', 'red', 'bold')} {text}")


def hint(text: str) -> None:
    print(f"  {paint(text, 'gray')}")


def box(title: str, rows: list[str], accent: str = "teal") -> None:
    width = max([visible_len(r) for r in rows] + [visible_len(title) + 4]) + 2
    line = lambda s: f"{paint('│', accent)} {s}{' ' * (width - visible_len(s) - 1)}{paint('│', accent)}"
    print(paint("╭─ ", accent) + paint(title, "bold") + " " + paint("─" * (width - visible_len(title) - 3) + "╮", accent))
    for r in rows:
        print(line(r))
    print(paint("╰" + "─" * width + "╯", accent))


def table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [max(visible_len(str(x)) for x in col) for col in zip(headers, *rows)]
    cell = lambda s, w: str(s) + " " * (w - visible_len(str(s)))
    print("  " + "  ".join(paint(cell(h, w), "gray", "bold") for h, w in zip(headers, widths)))
    for r in rows:
        print("  " + "  ".join(cell(c, w) for c, w in zip(r, widths)))


class Spinner:
    """Spinner animado numa linha só. Em saída não interativa, imprime linhas simples."""

    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, text: str):
        self.text, self.detail = text, ""
        self._stop, self._thread, self._done = threading.Event(), None, False
        self._animate = tty()

    def __enter__(self):
        if self._animate:
            sys.stdout.write("\x1b[?25l")
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        else:
            print(f"→ {self.text}…", flush=True)
        return self

    def _run(self):
        i = 0
        while not self._stop.is_set():
            self._draw(paint(self.FRAMES[i % len(self.FRAMES)], "teal", "bold"))
            i += 1
            time.sleep(0.08)

    def _draw(self, lead: str):
        room = term_width() - visible_len(self.text) - 6
        detail = self.detail.strip()
        if len(detail) > room:
            detail = detail[: max(room - 1, 0)] + "…"
        tail = f" {paint(detail, 'gray')}" if detail and room > 8 else ""
        sys.stdout.write(f"\r\x1b[2K{lead} {self.text}{tail}")
        sys.stdout.flush()

    def update(self, detail: str = "", text: str | None = None):
        self.detail = _ANSI.sub("", detail).replace("\r", "").strip()
        if text:
            self.text = text

    def _end(self, symbol: str, text: str | None):
        if self._done:
            return
        self._done = True
        self._stop.set()
        if self._thread:
            self._thread.join()
        if self._animate:
            sys.stdout.write("\r\x1b[2K\x1b[?25h")
        print(f"{symbol} {text or self.text}", flush=True)

    def ok(self, text: str | None = None):
        self._end(paint("✔", "green", "bold"), text)

    def warn(self, text: str | None = None):
        self._end(paint("!", "amber", "bold"), text)

    def fail(self, text: str | None = None):
        self._end(paint("✖", "red", "bold"), text)

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            self.fail()
        else:
            self.ok()
        return False


def ask(question: str, default: str = "") -> str:
    """Pergunta de texto com valor padrão. Sem terminal interativo, devolve o padrão."""
    if not interactive():
        return default
    shown = f" {paint('(' + default + ')', 'gray')}" if default else ""
    try:
        answer = input(f"{paint('?', 'teal', 'bold')} {paint(question, 'bold')}{shown} {paint('›', 'teal')} ").strip()
    except EOFError:
        print()
        return default
    except KeyboardInterrupt:
        print(f"\n{paint('Cancelado.', 'gray')}")
        raise SystemExit(130)
    return answer or default


def confirm(question: str, default: bool = True) -> bool:
    if not interactive():
        return default
    hint_ = "S/n" if default else "s/N"
    while True:
        answer = ask(f"{question} [{hint_}]", "").lower()
        if not answer:
            return default
        if answer in ("s", "sim", "y", "yes"):
            return True
        if answer in ("n", "nao", "não", "no"):
            return False


def choose(question: str, options: list[tuple[str, str, str]], default: int = 0) -> str:
    """Menu numerado. options = [(valor, rótulo, descrição)]. Sem terminal interativo, devolve o padrão."""
    if not interactive():
        return options[default][0]
    print(f"{paint('?', 'teal', 'bold')} {paint(question, 'bold')}")
    for i, (_, label, desc) in enumerate(options, 1):
        star = paint(" (padrão)", "gray") if i - 1 == default else ""
        print(f"   {paint(str(i), 'teal', 'bold')}  {paint(label, 'bold')}{star}")
        print(f"      {paint(desc, 'gray')}")
    while True:
        answer = ask("Escolha", str(default + 1)).lower()
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return options[int(answer) - 1][0]
        for value, label, _ in options:
            if answer in (value, label.lower()):
                return value
        warn(f"Digite um número de 1 a {len(options)}.")
