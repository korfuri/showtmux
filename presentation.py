import showtmux

class Presentation(showtmux.Presentation):
    def present(self):
        self.note("""Welcome to showtmux!

These are your speaker notes. This screen is for your eyes only. You should attach a tmux instance using the command-line displayed above, and show that tmux to your audience. You can also attach to that tmux session at any time in this terminal by pressing t.

This presentation will walk you through the basic features of showtmux.""")
        self.wait('Press enter to move to the next step. Press h for help.')
        self.cmd('# showtmux is an interactive terminal-based presentation tool')

        self.wait('We now demonstrate command interactivity by running ls and curl')
        self.cmd('# The lines you see me type are pre-recorded, but the commands are actually run, because this sends actual key presses to tmux.')
        self.sleep(0.5)
        self.cmd('date')
        self.cmd('uname -a')

        self.wait('We now demonstrate that interactive terminal apps work as well')
        self.cmd('emacs foo.cc')

        self.wait('We can type text')
        self.raw("""#include <iostream>

int main() {
  std::cout << "Hello, world!" << std::endl;
  return 0;
}
""")

        self.wait('We can send complex key presses, such as emacs shortcuts')
        self.note('Ctrl-x Ctrl-s to save, Ctrl-x Ctrl-c to quit emacs')
        self.keyseq(['C-x', 'C-s', 'C-x', 'C-c'])

        self.wait('And we can now run more commands')
        self.cmd('g++ -o foo foo.cc')
        self.cmd('./foo')

        self.chapter('integrations', 'Fancy tools')
        self.cmd('# showtmux can work with other cool terminal apps like mdp and caca-utils')
        self.sleep(0.5)
        self.cmd('# Let me show you!')
        
        self.wait('Displaying a picture with caca-view')
        self.show_picture('tux.png')

        self.wait('Other fancy libcaca demos work too')
        self.close_picture()
        self.cmd('cacademo')

        self.chapter('Usage', 'Using showtmux')
        self.cmd('# showtmux is very simple to use')
        self.cmd('# Here is the source code for this presentation')

        self.wait('We now show the source in emacs, with syntax highlighting')
        self.cmd('emacs presentation.py')

        self.wait('We simply inherit from showtmux.Presentation and define the script with present()')
        self.key('M-x')
        self.raw('credits-roll\n')

    def media(self):
        return ['tux.png', 'presentation.py']

    def dotfiles_templates(self):
        d = super(Presentation, self).dotfiles_templates()
        d['.emacs'] = """(defun credits-roll ()
  "Animate scrolling the current buffer until buffer-end."
  (interactive)
(setq scrolling-timer (run-at-time 0 0.02 (lambda () (if (eobp) (cancel-timer scrolling-timer) (next-line))))))
"""
        return d
        
p = Presentation("showtmux")
p.run()
