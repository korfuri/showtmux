#!/usr/bin/env python3
import curses
import curses.ascii
import logging
import pprint
import os
import random
import shlex
import subprocess
import string
import tempfile
import textwrap
import time

def readfile(f):
    return open(f, "rb").read()

def random_handle():
    return "".join(random.choice(string.ascii_lowercase) for _ in range(6))

class UserQuitException(Exception):
    """An exception class that signals that the user intends to quit presenting."""
    pass

class TmuxController(object):
    session_name = ""

    def __init__(self, session_name, tmux_conf="~/.tmux.conf", tmux_opts=None):
        self.session_name = session_name
        self.socket = session_name + ".sock"
        self.tmux_conf = tmux_conf
        self.kill_server()
        self.tmux("-f {} start-server".format(tmux_conf))
        self.logger = logging.getLogger(__name__)
        for k, v in tmux_opts.items():
            self.tmux(
                "set-option -g {option} {value}".format(
                    option=k, value=v,
                )
            )
        
    def debug(self):
        return os.environ.get("SHOWTMUX_DEBUG", False)

    def speedy(self):
        return os.environ.get("SHOWTMUX_SPEEDY", False)

    _tmux_escape_translation = str.maketrans({";": ";;"})

    def tmux_escape(self, s):
        return s.translate(self._tmux_escape_translation)

    def option(self, flag, value):
        if value is None:
            return ""
        return "{flag} {value}".format(flag=flag, value=value)

    def tmux(self, cmd, prefix=""):
        sh = "{prefix} tmux -L {socket} {cmd}".format(
            prefix=prefix, socket=self.socket, cmd=cmd
        )
        if self.debug():
            self.logger.debug('Running command: ', sh)
        result = None
        output = ""
        try:
            output = subprocess.check_output(sh, shell=True, stderr=subprocess.STDOUT)
            result = 0
        except subprocess.CalledProcessError as e:
            result = e.returncode
            output = e.output
        if self.debug():
            if result != 0:
                self.logger.debug("tmux returned code {}".format(result))
            if output:
                self.logger.debug("tmux output: " + output.decode("utf-8", errors="backslashescape"))


    def sendkey(self, key, target=None):
        self.tmux(
            "send-keys  {target} {keys}".format(
                target=self.option("-t", target),
                keys=self.tmux_escape(shlex.quote(key)),
            )
        )

    def sleep_between_keypresses(self):
        if self.speedy():
            return
        time.sleep(0.02 + random.gammavariate(2.0, 0.5) * 0.1)

    def t(self, keys, target=None, sleep=True):
        for k in keys:
            if sleep:
                self.sleep_between_keypresses()
            self.sendkey(k, target=target)

    def cmd(self, c, target=None):
        self.t(c)
        if c[-1] != "\n":
            self.t("\n", target=target)
        self.sleep_between_keypresses()

    def kill_server(self):
        self.tmux("kill-server")

    def new_session(self, window_name=None, shell_cmd=None, env=None, wd=None):
        env_prefix = ""
        if env is not None:
            env_prefix = "env {vars}".format(
                vars=" ".join(k + "=" + v for k, v in env.items())
            )
        newsess = "{f} new-session -d {s} {n} {c} {shell_cmd}".format(
            f=self.option("-f", self.tmux_conf),
            s=self.option("-s", self.session_name),
            n=self.option("-n", self.tmux_escape(window_name)),
            c=self.option("-c", wd),
            shell_cmd=format(shlex.quote(shell_cmd)) if shell_cmd is not None else "",
        )
        self.tmux(newsess, prefix=env_prefix)
        return (self.socket, self.session_name)

    def new_window(self, window_name, wd=None, shell_cmd=None):
        self.tmux(
            "new-window -d {c} {n} {shell_cmd}".format(
                c=self.option("-c", wd),
                n=self.option("-n", window_name),
                shell_cmd="" if shell_cmd is None else shlex.quote(shell_cmd),
            )
        )

    def select_window(self, window_name):
        self.tmux("select-window -t {}".format(window_name))




class Presentation(object):
    def dotfiles_templates(self):
        d = {
            ".tmux.conf": """## .tmux.conf
set-option -g status-interval 1
set-option -g status-left-length 30
set-option -g status-right '#[fg=cyan]%F %R'
set-option -g visual-activity on
set-option -g status-justify left
set-option -g status-bg black
set-option -g status-fg white
set-option -g message-bg white
set-option -g message-fg black
set-option -g update-environment "SSH_ASKPASS SSH_AUTH_SOCK SSH_AGENT_PID SSH_CONNECTION"
set-window-option -g window-status-current-fg red
set-window-option -g window-status-current-attr bright

set -g prefix C-a
unbind-key C-b
bind-key C-a send-prefix
bind C-n next
bind space next
bind C-space next
bind C-p prev
bind a last-window
set-window-option -g mode-keys emacs
setw -g aggressive-resize on
""",
            ".bashrc": r"""## .bashrc
shopt -s checkwinsize
PS1='\[\033[01;31m\]\u@\h\[\033[00m\]\$ '
""",
            # Pretend we used sudo before, so bash doesn't tell us how to do it
            ".sudo_as_admin_successful": "",
        }
        for i in self.media():
            d[os.path.basename(i)] = readfile(i)
        return d

    dotfiles = dict()

    def environment(self):
        return {
            # Overrides
            "HOME": self.wd,
            "DISPLAY": "",
            "HISTFILE": os.path.join(self.wd, ".bash_history.showtmux"),
            # Pass-throughs
            "PATH": '"$PATH"',
            "TERM": '"$TERM"',
            "COLORTERM": '"$COLORTERM"',
            "SHLVL": '"$SHLVL"',
            "LANG": '"$LANG"',
            "LC_MEASUREMENT": '"$LC_MEASUREMENT"',
            "LC_PAPER": '"$LC_PAPER"',
            "LC_MONETARY": '"$LC_MONETARY"',
            "LC_NAME": '"$LC_NAME"',
            "LC_ADDRESS": '"$LC_ADDRESS"',
            "LC_NUMERIC": '"$LC_NUMERIC"',
            "LC_TELEPHONE": '"$LC_TELEPHONE"',
            "LC_IDENTIFICATION": '"$LC_IDENTIFICATION"',
            "LC_TIME": '"$LC_TIME"',
            "LANGUAGE": '"$LANGUAGE"',
        }

    def shell(self):
        return "bash --rcfile {rcfile}".format(rcfile=self.dotfiles[".bashrc"])

    def __init__(self, session_handle=random_handle()):
        self.session_handle = session_handle
        self.wd = tempfile.mkdtemp()
        self.make_dotfiles()
        self.tmux = None
        
    def make_dotfiles(self):
        for filename, contents in self.dotfiles_templates().items():
            self.dotfiles[filename] = os.path.join(self.wd, filename)
            if type(contents) == bytes:
                mode = "wb"
            else:
                mode = "w"
            with open(self.dotfiles[filename], mode) as fh:
                fh.write(contents)

    def banner(self, handle, bannertext):
        self.tmux.tmux(
            "send-keys {t} 'clear; figlet -c -W  '{text}' \n'".format(
                t=self.tmux.option("-t", handle), text=shlex.quote(bannertext)
            )
        )

    def wait(self, note):
        self.log('Next:\n{}\n'.format(note))
        self.setstatus(self.PAUSED)
        self.await_keypress()
        self.setstatus(self.PLAYING)
        
    PAUSED  = u'\U000023f8\tPaused'
    PLAYING = u'\U000023e9\tPlaying'
    ENDED   = u'\U0001f3c1\tComplete'
    
    def setstatus(self, status):
        if self.tmux is not None:
            line = '{status}\t$ tmux -L {socket} attach'.format(status=status, socket=self.tmux.socket)
        else:
            line = status
        self.statuswin.addstr(0, 0, line, curses.A_BOLD)
        self.statuswin.clrtoeol()
        self.statuswin.refresh()

    def logseparator(self):
        hintline = ['n', 'ext/attach ', 't', 'mux/', 'q', 'uit/', 'h', 'elp']
        hintline_len = len(''.join(hintline))
        hintline_starts = max(0, curses.COLS - hintline_len)
        x = hintline_starts
        if hintline_starts > 5:
            self.logwin.addstr(''.join('=' for _ in range(hintline_starts - 1)), curses.A_DIM)
            self.logwin.addch(' ')
        else:
            self.logwin.addstr(''.join(' ' for _ in range(hintline_starts)))
        bold = True
        for h in hintline:
            self.logwin.addstr(h, curses.A_BOLD if bold else curses.A_DIM)
            bold = not bold
            # self.logwin.addstr(0, x), hintline)
        self.logwin.refresh()
        
    def log(self, text, *args):
        self.logwin.addstr(text, *args)
        self.logwin.refresh()
        
    def await_keypress(self):
        self.logseparator()
        while True:
            c = self.scr.getch()
            if c == curses.KEY_RESIZE:
                pass
            elif c in [ord('n'), ord('l'), ord('j'), curses.KEY_DOWN, curses.KEY_RIGHT, curses.KEY_NPAGE, curses.KEY_ENTER, ord(' '), ord('\n'), ord('\r')]:
                return  # We simply return and continue with the script
            elif c in [ord('?'), ord('h')]:
                self.log('showtmux help\n', curses.A_BOLD)
                self.log("""Available keys:
next step             n, l, j, space, enter, Right/Down, PgDown
quit                  q
help (this text)      ?, h
enter tmux            t, C-a, C-b      Use <prefix>-d to detach tmux
kill server           K                Disconnects all clients and quits
""")
            elif c == ord('q'):
                raise UserQuitException()
            elif c in [ord('t'), curses.ascii.ctrl(ord('a')), curses.ascii.ctrl(ord('b'))]:
                self.enter_tmux()
            elif c == ord('K'):
                self.tmux.kill_server()
                raise UserQuitException()
            else:
                self.log('Unrecognized key. Press "h" for help.\n')

    def enter_tmux(self):
        class SuspendCurses(object):
            def __enter__(self):
                curses.endwin()

            def __exit__(self, exc_type, exc_val, tb):
                scr = curses.initscr()
                scr.refresh()
                curses.doupdate()

        with SuspendCurses():
            os.system('tmux -L {socket} attach'.format(socket=self.tmux.socket))

    def linewrap(self, text):
        """Wrap text to fit in the current terminal size.

        This respects existing newlines, and wraps each paragraph separately with textwrap.wrap."""
        return '\n'.join(['\n'.join(textwrap.wrap(l, curses.COLS - 1, replace_whitespace=False)) for l in text.split('\n')])
            
    def note(self, note):
        self.log('Note:\n{}\n'.format(self.linewrap(note)))

    def sleep(self, duration):
        time.sleep(duration)

    def chapter(self, handle, title=None, shell_cmd=None):
        if shell_cmd is None:
            shell_cmd = self.shell()
        self.wait("CHAPTER: [{}] {}".format(handle, title or ''))
        self.tmux.new_window(handle, wd=self.wd, shell_cmd=shell_cmd)
        if title is not None:
            self.banner(handle, title)
        self.tmux.select_window(handle)

    def cmd(self, cmd):
        self.tmux.cmd(cmd)

    def raw(self, text, sleep=False):
        self.tmux.t(text, sleep=sleep)

    def key(self, key):
        self.tmux.sendkey(key)

    def keyseq(self, keys):
        for k in keys:
            self.key(k)

    def run(self):
        curses.wrapper(self.run_under_curses)

    def run_under_curses(self, scr):
        self.scr = scr
        self.statuswin = curses.newwin(1, curses.COLS, 0, 0)
        self.logwin = curses.newwin(curses.LINES - 1, curses.COLS, 1, 0)
        self.logwin.scrollok(True)
        self.scr.refresh()
        self.setstatus(self.PAUSED)
        self.tmux = TmuxController(
            self.session_handle,
            tmux_conf=self.dotfiles[".tmux.conf"],
            tmux_opts={
                "default-command": shlex.quote(self.shell()),
                # This option works for tmux <1.9. For tmux >1.9, the
                # path is passed in new-window commands.
                "default-path": self.wd,
            },
        )
        self.tmux.new_session(
            window_name=self.session_handle,
            wd=self.wd,
            env=self.environment(),
            shell_cmd=self.first_window_command(),
        )
        self.log("showtmux session created. Run: tmux -L {socket} attach\n".format(socket=self.tmux.socket))
        try:
            self.present()
            while True:
                self.setstatus(self.ENDED)
                self.await_keypress()
                self.log('The presentation has ended. Press "t" to enter tmux, or "q" to quit.\n')

        except UserQuitException:
            return
        except Exception as e:
            self.tmux.kill_server()
            raise e

    def first_window_command(self):
        return self.shell()

    def present(self):
        raise NotImplementedError("Presentations must define the present() method")

    def media(self):

        """Child classes may override this method to provide a list of media
        files (e.g. images) to be copied to the presentation's working
        directory.

        """
        return []

    cacaview_fullscreen = "f"
    cacaview_quit = "q"

    def show_picture(self, path):
        # tmux will set TERM=screen on the first command, because no
        # client is connected to pass another TERM. We assume that
        # people presenting this will have a modern vt100 and
        # forcefully set TERM=xterm-256color. If this causes issues in
        # your presentation, override this function and remove
        # TERM=xterm-256color from the line below.
        self.raw("TERM=xterm-256color cacaview {path}\n".format(path=path))
        self.keyseq(self.cacaview_fullscreen)

    def close_picture(self):
        self.keyseq(self.cacaview_quit)



class WithMdp(object):
    def first_window_command(self):
        # tmux will set TERM=screen on the first command, because no
        # client is connected to pass another TERM. We assume that
        # people presenting this will have a modern vt100 and
        # forcefully set TERM=xterm-256color. If this causes issues in
        # your presentation, override this function and remove
        # TERM=xterm-256color from the line below.
        return "TERM=xterm-256color mdp {}".format(self.dotfiles["slides.md"])

    def mdp_next(self):
        self.tmux.sendkey("j")

    def mdp_prev(self):
        self.tmux.sendkey("l")

    def mdp_quit(self):
        self.tmux.sendkey("q")

    def mdp_first(self):
        self.tmux.sendkey("g")

    def mdp_last(self):
        self.tmux.sendkey("G")

    def dotfiles_templates(self):
        t = super(WithMdp, self).dotfiles_templates()
        t["slides.md"] = readfile(self.slides_file())
        return t

    def slides_file(self):
        raise NotImplementedError(
            'WithMdp Presentations must provide a slides_file (e.g. return "slides.md")'
        )


if __name__ == "__main__":
    print(
        """This module is not meant to be run.

Instead, you should create a presentation.py file as such:

import tmux

class Presentation(tmux.Presentation):
    def present(self):
        self.cmd('echo hello, world!')

p = Presentation()
p.run()
"""
    )
