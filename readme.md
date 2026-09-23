# Tandem Tales Salience Agent (Python)

This repository provides a [Tandem Tales](https://sgware.github.io/tt) agent in
Python that chooses actions using recency-based salience:

- Action parameters start at zero salience.
- Parameters seen during a turn are refreshed to `1.0`; unseen parameters decay
	by `0.5` per turn.
- The tracked parameters are `player`, `gamemaster`, `barista`, `coffee`,
	`herbal tea`, `shop`, `outside`, and `money`.
- The agent chooses the action with the highest combined parameter salience.
- Ties prefer actions involving the player, then resolve randomly.

This repository exists to be forked by anyone developing their own Python agent.

The [Docker image](Dockerfile) in this project defines a
[container](https://www.docker.com/resources/what-container/) (basically a
lightweight virtual machine) that has [Python](http://python.org), the
[Tandem Tales Python client library](https://github.com/sgware/tt-client-python),
and [the code for this agent](root/app) already installed. Docker makes it easy
to package your agent so others can easily run it without worrying about
dependencies, versions numbers, etc.

The [Docker Compose configuration](compose.yaml) makes it easy to run your agent
alongside a local instance of the
[Tandem Tales Web Server](https://github.com/sgware/tt-web) so your agent has
something to connect to and so you can test your agent in a web browser. Docker
Compose handles generating the self-signed SSL certificates, the network
configuration, setting up the Tandem Tales database, and running the web server
for you so you can focus on developing your agent.

## Download, Build, and Run

To download and run this agent, you need [Git](http://git-scm.com),
[Docker](http://www.docker.com), and
[Docker Compose](http://docs.docker.com/compose) installed. You *don't* need
Python or any libraries installed, because all of that is already in the Docker
image.

To download and run this agent alongside a local instance of the Tandem Tales
web server, open a console and type:
```
git clone https://github.com/<your-github-username>/tt-salience-agent.git
cd tt-salience-agent
copy .env.example .env
docker compose build
docker compose up
```
Then open a web browser and go to [http://localhost](http://localhost) to see a
local version of the Tandem Tales website running on your computer where you can
play an interactive story with this agent.

## Make This Agent Your Own

### Name and Settings

The first thing you should do to customize this agent is to change its name.
Find the agent constructor in [`main.py`](root/app/main.py):
```
def __init__(self, url='localhost', port=tt.DEFAULT_PORT):
	super().__init__('salience', None, None, None, None, None, url, port)
```
Change the string `'salience'` to your agent's new name.

You also need to set this new name name in the environment variable file
[`.env`](.env). Change the line that looks like this:
```
name="salience"
```
You should also change the `title` and `description` variables to something that
better fit your new agent.

Now stop the container (`ctrl+C` in the terminal, or use the stop button in the
GUI). Rebuild your image and start it back up with one line:
```
docker compose up --build
```
Go to [https://localhost/play](https://localhost/play) to see your new agent's
title and description on the list of available agents.

This random agent can play in any story world in either role (player or game
master). Most agents aren't that flexible. Suppose your agent is only designed
to play in the `tutorial` world and only in the `GAME_MASTER` role. You'll need
to change your agent constructor to look something like this:
```
def __init__(self, url='localhost', port=tt.DEFAULT_PORT):
    super().__init__('barista', None, 'tutorial', tt.GM, None, None, url, port)
```
And don't forget to change the [`.env`](.env) file to match:
```
name="barista"
title="The Barista"
description="This agent plays as the barista in the tutorial."
world="tutorial"
role="GAME_MASTER"
```

### Agent Code

Now it's time to start changing the code. The random agent is pretty simple. In
fact, it's [just one file](root/app/main.py)!

The only method a Tandem Tales
[client](https://sgware.github.io/tt-client-python/api/#tt.Client) *needs* to
implements is
[`on_choice`](https://sgware.github.io/tt-client-python/api/#tt.Client.on_choice).
Every time it is your agent's turn in the story, this method gets called. The
`status` object has
[a ton of useful information](https://sgware.github.io/tt/protocol.html#status-object)
in it, including:
- `status['history']` has all the turns that have happened so far in the story.
- `status['state']` tells you the current value of all the world's variables.
- `status['descriptions']` has natural language descriptions of all the objects
  your agent can currently see.
- `status['choices']` is a list of the things you can do next.

The `on_choice` method should return the integer index (starting at 0) of the
choice from `status['choices']` that you want to make.

Change the `on_choice` method like this to print the `world` and `status`
objects to file so you can see all the useful information in them:
```
def on_choice(self, status):
	with open("world.json", "w") as file:
		json.dump(self.world, file, indent=4)
	with open("status.json", "w") as file:
		json.dump(status, file, indent=4)
	...
```
Don't forget to `import json` at the top of the file too.

Toward the bottom of [`main.py`](root/app/main.py) you'll see the
`RandomAgentFactory` class. An agent only plays one story session and then
closes. If you want to be able to play with your agent more than once, you need
a factory that makes new agents on demand.

Good news! Almost all of that code is already written for you. All you have to
do is extend
[`tt.Factory`](https://sgware.github.io/tt-client-python/api/#tt.Factory) and
implement the
[`create`](https://sgware.github.io/tt-client-python/api/#tt.Factory.create)
method like this:
```
class RandomAgentFactory(tt.Factory):
    def create(self):
        return RandomAgent()
```

At the bottom of [`main.py`](root/app/main.py) you'll see the code that makes a
new factory and runs it:
```
factory = RandomAgentFactory()
factory.run()
```

A factory starts by making one agent. That agent waits on the server for a
partner to play with. As soon as it finds a partner, the factory will make a new
agent to take the old agent's place waiting on the server. That way, you never
run out of agents to play with.

By the way, you might want to change the class names `RandomAgent` and
`RandomAgentFactory` to something that better fits your agent.

### Agent Lifecycle Methods

The only methods you *need* to implement are `on_choice` in your agent and
`create` in your factory. But there's a bunch of other methods that get called
at important moments in the agent's life that you might find helpful.

For example, if your agent need to load some resource before it joins the
server, you could do that in the
[`on_connect`](https://sgware.github.io/tt-client-python/api/#tt.Client.on_connect)
method which runs after opening the connection but before waiting for a partner.
If you need to clean up that resource before the agent stops, override
[`on_disconnect`](https://sgware.github.io/tt-client-python/api/#tt.Client.on_disconnect).
If your agent can play in any story world, you might want to override the
[`on_start`](https://sgware.github.io/tt-client-python/api/#tt.Client.on_start)
method, which runs as soon as the agent's session starts and tells you all the
details of the story world, your agent's role, and the starting state of the
world.

Got a resource you need to load once and share across all agents? Load it in
your factory's
[`on_start`](https://sgware.github.io/tt-client-python/api/#tt.Factory.on_start)
method and clean it up in
[`on_stop`](https://sgware.github.io/tt-client-python/api/#tt.Factory.on_stop).

Check out the
[Tandem Tales Python Client](https://sgware.github.io/tt-client-python/) library
for documentation on the factory and agent lifecycles.

### Adding and Changing Files

Docker makes it easy to add new files to your agent. If you look at the
[Dockerfile](Dockerfile), you'll see this line:
```
COPY ./root /
```
That line copies all the files and directories in this project's [`root`](root)
folder into the container's root `/` directory. So if you add the file
`root/app/utility.py` to this project, you'll find it at `/app/utility.py` in
the container. If you add `root/usr/local/bin/script.sh` to this project, you'll
find it at `/usr/local/bin/script.sh` in the container.

You can stop Docker and re-start it every time you make changes to the code, but
that gets tedious. When you launch this container using the Docker Compose
commands shown above, it mounts the [`root/app`](root/app) directory as a
[volume](https://docs.docker.com/engine/storage/volumes/). That means everything
in that directory is actually a local file on your computer. So you can edit
`main.py` while the container is running and the container sees it instantly.
Similarly, any files that get created (like `status.json` from earlier) are on
your computer and will stay there when the container stops.

To make changes to the agent without stopping the container, start the project
like this:
```
docker compose up -d && docker compose attach agent
```
The `-d` flag starts the containers detached. Then the `attach` command attaches
just to the container with your agent in it, and now you're using the same
`bash` shell that is running your Python program.

You can press `ctrl+C` to stop your agent factory. The factory doesn't stop
immediately; it waits for all currently running sessions to end. So if you've
got a session open in a web browser, close that tab so the factory can stop.
Then make some changes to the code using your favorite text editor on your
computer. Now type this to start the factory back up:
```
python3 main.py
```

### Using the External API

Does your agent need to use a Large Language Model (LLM)? Modern LLMs are too
large to pack into a container and too slow to run without a high-end GPU.
Tandem Tales agents come with a built-in way to use an external LLM for
expensive operations like text embedding and chat completion, but first you
will need an [API Key](https://en.wikipedia.org/wiki/API_key), which is like a
user name and password all in one.

Currently, Tandem Tales uses the [OpenRouter](https://openrouter.ai) API, which
means you'll need to create an account on that website, follow the instructions
to get an API key (which is a long string of random letters and numbers), and
set up some way to pay for usage.

You can tell your agent about your API key by setting the `apikey` environment
variable in [`.env`](.env):
```
apikey=0123456789abcdefghijklmnopqrstuvwxyz
```
> [!WARNING]
> Never share your API key! Never hard-code an API key into your agent's source
> code. If you set your key in the `.env` file, never commit that file to a
> public repository like GitHub, and never send the file to others. If you are
> sending your agent to someone else to run, don't include your API key. They
> should provide their own key.

Now you can modify your `on_choice` method to use an LLM to make a random choice
like this:
```
def on_choice(self, status):
	count = len(status['choices'])
	prompt = f"Choose a number between 0 and {count} inclusive."
	response = self.external.complete(prompt)
	return int(response)
```

But watch out! This code assumes you'll get just an interger back as the
response, but you might get something like `"I choose 3"`. Your code will need
to handle those cases.

Check out the
[Tandem Tales Python Client documentation](https://sgware.github.io/tt-client-python/api/#tt.ExternalAPI)
to see all the options available in the external API.

## Version History

- 0.9.0: First public release.

## License

The Tandem Tales Random Agent (Python) was developed by Stephen G. Ware PhD.
in the [Computer Science department](http://cs.uky.edu) at the
[University of Kentucky](http://uky.edu). Development was sponsored in part by
the [US National Science Foundation](http://www.nsf.gov),
([Grant #2145153](https://www.nsf.gov/awardsearch/show-award?AWD_ID=2145153))
and the [US Army Research Office](http://arl.devcom.army.mil/who-we-are/aro)
(Grant W911NF-24-1-0195).

The code in this project is free and open source. It is released under the
[GNU General Public License version 3.0](https://choosealicense.com/licenses/gpl-3.0/)
(GPL 3.0). You are free to share and modify this software, even for commercial
purposes, as long as you give credit to the original creators and you also
release your modifications under the GPL 3.0 license. See the
[license file](license.txt) for details. The University of Kentucky retains all
rights not specifically granted.

Docker, Docker Compose, and the software used in Docker containers have their
own licenses that are not necessarily covered by GPL-3.0.