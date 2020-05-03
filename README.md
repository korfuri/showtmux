# showtmux

Showtmux creates terminal-based interactive presentations. It can be
used to script terminal-based demos ahead of time, and play them in
tmux.

## How it works

You create a presentation in code as such:

```python
from showtmux import Presentation

class MyPresentation(showtmux.Presentation):
	def present(self):
		self.chapter('Welcome to my talk')
		self.wait('This text is visible only to you. Use this to keep speaker notes')
		self.cmd('echo "I am typing this command by hand as you can see")
		
		self.chapter('That's all folks!')

p = MyPresentation()
p.run()
```

## Requirements

* tmux

Optional (but recommended):

* `pip install sty` for colored output in the speaker terminal;
* `caca-utils` to display images in the terminal;
* `mdp` if you want to have non-interactive, really-cool looking
  slides in addition to your interactive demo.
