#!/usr/bin/env python3
import curses
import curses.ascii
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

class FatalError(Exception):
    """An exception used when showtmux needs to terminate, but tmux is still running."""
    def __init__(self, connectline):
        super(FatalError, self).__init__(
            ('showtmux encountered an error, but tmux is still '
             'running. You can connect to it by running '
             '`{connectline}` and finish the presentation by hand.'.
             format(connectline=connectline)))

class Presentation(object):
    """Presentations contain all the state and methods to manage a
    tmux-based presentation.

    This class is meant to be inherited from and extended, overriding
    at least the present() method.

    This class includes several different things:
    - internal state management;
    - helpers to communicate with tmux, including state;
    - UI management using curses;
    - helpers for child classes, e.g. to run commands in tmux;
    - methods that child classes must of may override.

    The class is organized in the order above.
    """

    ###################################
    #### Internal state management ####
    ###################################

    def __init__(self, session_handle=random_handle()):
        """Instantiates a Presentation.

        tmux is not started at this stage, see _tmux_init().
        """
        self.session_handle = session_handle
        self.wd = tempfile.mkdtemp()
        self._make_dotfiles()
        self.socket = None

    def run(self):
        """Entry-point from user code. See _run_under_curses."""
        curses.wrapper(self._run_under_curses)

    def _run_under_curses(self, scr):
        """Main handler for showtmux.

        This sets up the curses UI, initializes tmux, and calls the
        user's present() method to run the presentation. After the
        presentation has comlpleted, this becomes an eventloop until
        the user decides to close showtmux.

        If present() raises an exception, this will terminate the
        """
        self.scr = scr
        self.statuswin = curses.newwin(1, curses.COLS, 0, 0)
        self.logwin = curses.newwin(curses.LINES - 1, curses.COLS, 1, 0)
        self.logwin.scrollok(True)
        self.scr.refresh()
        self._set_status(self.PAUSED)
        self._tmux_init(
            tmux_conf=self.dotfiles[".tmux.conf"],
            tmux_opts={
                "default-command": shlex.quote(self.shell()),
                # This option works for tmux <1.9. For tmux >1.9, the
                # path is passed in new-window commands.
                "default-path": self.wd,
            },
        )
        self._tmux_new_session(
            window_name=self.session_handle,
            wd=self.wd,
            env=self.environment(),
            shell_cmd=self.first_window_command(),
        )
        self._log("showtmux session created. Run: tmux -L {socket} attach\n".format(socket=self.socket))
        try:
            self.present()
            while True:
                self._set_status(self.ENDED)
                self._await_keypress()
                self._log('The presentation has ended. Press "t" to enter tmux, or "q" to quit.\n')

        except UserQuitException:
            return
        except Exception as e:

            self._tmux_kill_server()  ## TODO
            raise FatalError('tmux -L {socket} attach'.format(socket=self.socket)) from e

    def _speedy(self):
        """Returns true if fast-typing mode is active."""
        return os.environ.get("SHOWTMUX_SPEEDY", '0') != '0'

    dotfiles = dict()
    def _make_dotfiles(self):
        """Copies the actual dotfiles and media files to the working directory."""
        for filename, contents in self.dotfiles_templates().items():
            self.dotfiles[filename] = os.path.join(self.wd, filename)
            if type(contents) == bytes:
                mode = "wb"
            else:
                mode = "w"
            with open(self.dotfiles[filename], mode) as fh:
                fh.write(contents)

    ####################################
    #### tmux communication helpers ####
    ####################################

    def tmux(self, cmd, prefix=""):
        """Invokes a tmux subcommand.

        If prefix is provided, prepend this. This is useful to invoke
        tmux with a specific environment (useful e.g. when creating a
        new session).

        """
        assert(self.socket is not None)
        sh = "{prefix} tmux -L {socket} {cmd}".format(
            prefix=prefix, socket=self.socket, cmd=cmd
        )
        self._debug('Running command: {}'.format(sh))
        result = None
        output = ""
        try:
            output = subprocess.check_output(sh, shell=True, stderr=subprocess.STDOUT)
            result = 0
        except subprocess.CalledProcessError as e:
            result = e.returncode
            output = e.output
        if result != 0:
            self._debug("tmux returned code {}".format(result))
        if output:
            self._debug("tmux output: " + output.decode("utf-8", errors="backslashescape"))


    def _tmux_new_session(self, window_name=None, shell_cmd=None, env=None, wd=None):
        """Create a new tmux session.

        This sets the default path, default shell, and environment for
        the session. It also applies global options passed to
        _init_tmux previously.

        """
        env_prefix = ""
        if env is not None:
            env_prefix = "env {vars}".format(
                vars=" ".join(k + "=" + v for k, v in env.items())
            )
        newsess = "{f} new-session -d {s} {n} {c} {shell_cmd}".format(
            f=self._tmux_option("-f", self.tmux_conf),
            s=self._tmux_option("-s", self.session_handle),
            n=self._tmux_option("-n", self._tmux_escape(window_name)),
            c=self._tmux_option("-c", wd),
            shell_cmd=format(shlex.quote(shell_cmd)) if shell_cmd is not None else "",
        )
        self.tmux(newsess, prefix=env_prefix)
        for k, v in self._tmux_opts.items():
            self.tmux("set-option -g {option} {value}".format(option=k, value=v))
        return (self.socket, self.session_handle)

    def _tmux_init(self, tmux_conf="~/.tmux.conf", tmux_opts=None):
        """Initializes tmux for this presentation.

        This sets the socket name, kills any existing server on that
        socket, starts a new server, and saves global options to be
        used by all sessions we create on this server.

        """
        self.socket = self.session_handle
        self.tmux_conf = tmux_conf
        self._tmux_kill_server()  # TODO make this optional
        self.tmux("-f {} start-server".format(tmux_conf))
        self._tmux_opts = tmux_opts

    _tmux_escape_translation = str.maketrans({";": ";;"})
    def _tmux_escape(self, s):
        """Escapes a list of key codes for tmux.

        This replaces ';' (e.g. in a command line) with ';;' which is
        its escaped form for tmux. This is needed because both shells
        and tmux use ';' as a command separator.

        """
        return s.translate(self._tmux_escape_translation)

    def _tmux_option(self, flag, value):
        """Returns '-flag valud' is value is not None."""
        if value is None:
            return ""
        return "{flag} {value}".format(flag=flag, value=value)

    def _tmux_sendkey(self, key, target=None):
        """Sends one or more keypresses to tmux."""
        self.tmux(
            "send-keys  {target} {keys}".format(
                target=self._tmux_option("-t", target),
                keys=self._tmux_escape(shlex.quote(key)),
            )
        )

    def _tmux_kill_server(self):
        """Kill this tmux server, disconnecting all clients."""
        self.tmux("kill-server")

    def _sleep_between_keypresses(self):
        """Sleeps between keypresses to simulate a human typing."""
        # TODO: refactor away, and make delays user-configurable.
        if self._speedy():
            return
        time.sleep(0.01 + random.gammavariate(2.0, 0.5) * 0.07)

    #######################
    #### UI management ####
    #######################

    def _debug(self, message):
        """Logs a message (if in debug mode)."""
        if os.environ.get("SHOWTMUX_DEBUG", False):
            self.logwin.addstr(message, curses.COLOR_RED)
            self.logwin.addch('\n')
            self.logwin.refresh()

    PAUSED  = u'\U000023f8\tPaused'
    PLAYING = u'\U000023e9\tPlaying'
    ENDED   = u'\U0001f3c1\tComplete'

    def _set_status(self, status):
        """Set the status-line."""
        if self.socket is not None:
            line = '{status}\t$ tmux -L {socket} attach'.format(status=status, socket=self.socket)
        else:
            line = status
        self.statuswin.addstr(0, 0, line, curses.A_BOLD)
        self.statuswin.clrtoeol()
        self.statuswin.refresh()

    def _log_separator(self):
        """Log a horizontal separator with a prompt for common actions."""
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

    def _log(self, text, *args):
        """Log a line to the main window."""
        self.logwin.addstr(text, *args)
        self.logwin.refresh()

    def _await_keypress(self):
        """Wait for speaker interaction, and perform any requested actions.

        This returns when the speaker wants to continue to the next slide.
        """
        self._log_separator()
        while True:
            c = self.scr.getch()
            if c == curses.KEY_RESIZE:
                pass
            elif c in [ord('n'), ord('l'), ord('j'), curses.KEY_DOWN, curses.KEY_RIGHT, curses.KEY_NPAGE, curses.KEY_ENTER, ord(' '), ord('\n'), ord('\r')]:
                return  # We simply return and continue with the script
            elif c in [ord('?'), ord('h')]:
                self._log('showtmux help\n', curses.A_BOLD)
                self._log("""Available keys:
next step             n, l, j, space, enter, Right/Down, PgDown
quit                  q
help (this text)      ?, h
enter tmux            t, C-a, C-b      Use <prefix>-d to detach tmux
kill server           K                Disconnects all clients and quits
""")
            elif c == ord('q'):
                raise UserQuitException()
            elif c in [ord('t'), curses.ascii.ctrl(ord('a')), curses.ascii.ctrl(ord('b'))]:
                self._enter_tmux()
            elif c == ord('K'):
                self._tmux_kill_server()
                raise UserQuitException()
            else:
                self._log('Unrecognized key. Press "h" for help.\n')

    def _enter_tmux(self):
        """Attach to tmux.

        Detach from tmux to come back to showtmux.

        This suspends the curses window temporarily.
        """
        class SuspendCurses(object):
            def __enter__(self):
                curses.endwin()

            def __exit__(self, exc_type, exc_val, tb):
                scr = curses.initscr()
                scr.refresh()
                curses.doupdate()

        with SuspendCurses():
            os.system('tmux -L {socket} attach'.format(socket=self.socket))

    def _linewrap(self, text):
        """Wrap text to fit in the current terminal size.

        This respects existing newlines, and wraps each paragraph separately with textwrap.wrap."""
        return '\n'.join(['\n'.join(textwrap.wrap(l, curses.COLS - 1, replace_whitespace=False)) for l in text.split('\n')])

    ###################################
    #### Helpers for child classes ####
    ###################################

    def raw(self, keys, target=None, sleep=True):
        """Send a string or multiple keypresses to tmux.

        This can optionally sleep between keypreses.

        """
        for k in keys:
            if sleep:
                self._sleep_between_keypresses()
            self._tmux_sendkey(k, target=target)

    def cmd(self, c, target=None):
        """Send a command-line to tmux.

        This is like raw() but it adds a trailing \n at the end if
        needed.

        """
        self.raw(c)
        if c[-1] != "\n":
            self.raw("\n", target=target)


    def banner(self, handle, bannertext):  # TODO redo this
        self.tmux(
            "send-keys {t} 'clear; figlet -c -W  '{text}' \n'".format(
                t=self._tmux_option("-t", handle), text=shlex.quote(bannertext)
            )
        )

    def wait(self, note):
        """Display a note, and wait for speaker interaction."""
        self._log('Next:\n{}\n'.format(note))
        self._set_status(self.PAUSED)
        self._await_keypress()
        self._set_status(self.PLAYING)

    def new_window(self, window_name, wd=None, shell_cmd=None):
        """Create a new tmux window.

        If the working directory or shell is omitted, tmux will make
        them inherit from the session's defaults.

        """
        self.tmux(
            "new-window -d {c} {n} {shell_cmd}".format(
                c=self._tmux_option("-c", wd),
                n=self._tmux_option("-n", window_name),
                shell_cmd="" if shell_cmd is None else shlex.quote(shell_cmd),
            )
        )

    def select_window(self, window_name):
        """Switch to the given tmux window."""
        self.tmux("select-window -t {}".format(window_name))


    def note(self, note):
        """Display a speaker note."""
        self._log('Note:\n{}\n'.format(self._linewrap(note)))

    def sleep(self, duration):
        """Sleeps for the given time.

        Consider using wait() instead if you are blocking until a
        program has completed.

        """
        time.sleep(duration)

    def chapter(self, handle, title=None, shell_cmd=None):
        """Opens a new window, named after this chapter's handle.

        If a title is passed, display it with figlet.

        If a shell_cmd is passed, run that in the window instead of
        the default shell. It doesn't need to be a shell.

        """
        if shell_cmd is None:
            shell_cmd = self.shell()
        self.wait("CHAPTER: [{}] {}".format(handle, title or ''))
        self.new_window(handle, wd=self.wd, shell_cmd=shell_cmd)
        if title is not None:
            self.banner(handle, title)
        self.select_window(handle)

    def key(self, key):
        """Sends a single keypress to tmux."""
        self._tmux_sendkey(key)

    def keyseq(self, keys):
        """Sends a list of keypresses to tmux."""
        for k in keys:
            self.key(k)

    cacaview_fullscreen = "f"
    cacaview_quit = "q"

    def show_picture(self, path):
        # tmux will set TERM=screen on the first command, because no
        # client is connected to pass another TERM. We assume that
        # people presenting this will have a modern vt100 and
        # forcefully set TERM=xterm-256color. If this causes issues in
        # your presentation, override this function and remove
        # TERM=xterm-256color from the line below.
        self.raw("TERM=xterm-256color cacaview {path}\n".format(path=path), sleep=False)
        self.keyseq(self.cacaview_fullscreen)

    def close_picture(self):
        self.keyseq(self.cacaview_quit)

    #############################
    #### Overridable methods ####
    #############################


    def present(self):
        """Overridable; the main script for the presentation."""
        raise NotImplementedError("Presentations must define the present() method")

    def first_window_command(self):
        """Override to use a different shell in the first window.

        The first window is created before present() is called, with
        the session. By default it will use the defined shell(), but
        if you want to present something other than a shell first, you
        need to override this.

        """
        return self.shell()

    def media(self):
        """Child classes may override this method to provide a list of media
        files (e.g. images) to be copied to the presentation's working
        directory.

        """
        return []


    def dotfiles_templates(self):
        """Creates initial files in the working directory.

        To add your own dotfiles in your presentation, override this
        in your class as such:

        class MyPresentation(showtmux.Presentation):
          def dotfiles_templates(self):
            d = super(MyPresentation, self).dotfiles_templates()
            d['.mydotfile'] = '...'
            return d

        You can also use this to override the provided .tmux.conf and
        .bashrc. We include .sudo_as_admin_successful because some
        versions of bash will display a message explaining how to use
        sudo if you've never sudo'd before.

        """
        d = {
            ".tmux.conf":

        """## .tmux.conf
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
    def environment(self):
        """Defines the environment tmux windows spawn with.

        Users may override this."""
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
        """Defines the default shell tmux windows spawn with.

        Users may override this.

        If first_window_command is not overriden, the first window in
        a presentation spawns with this shell as well.

        """
        return "bash --rcfile {rcfile}".format(rcfile=self.dotfiles[".bashrc"])



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
        self._tmux_sendkey("j")

    def mdp_prev(self):
        self._tmux_sendkey("l")

    def mdp_quit(self):
        self._tmux_sendkey("q")

    def mdp_first(self):
        self._tmux_sendkey("g")

    def mdp_last(self):
        self._tmux_sendkey("G")

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
