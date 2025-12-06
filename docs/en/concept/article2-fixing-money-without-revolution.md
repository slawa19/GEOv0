# Money is Just Information. Let's Take It Back Under Our Control

**Why we're building GEO — a mutual credit network that can change your district's economy without waiting for Central Bank permission.**

Imagine typical situation in small town or even your residential block. There's baker making excellent bread, but needs roof repair. There's roofer with no orders now, but needs bread and English lessons for child. There's English teacher dreaming of fresh croissants. All have hands, skills, time and desire to work. But they all sit doing nothing. Why?

Because they lack mediator. They lack "money".

We're used to thinking money is some objective resource, like water or electricity, that "comes" from outside (from bank, employer, government). If no money, economic life stops, even if needs and opportunities haven't gone anywhere. This is absurd situation resembling company of friends at poker table who can't start game because nobody has plastic chips. Though they have cards, table and desire to play, they sit waiting for someone to bring them plastic.

GEO project we're now reviving and rethinking is attempt to say: we don't need "chips" from outside. We can create our own accounting system based on trust and launch community economy independently.

### "Leaky Bucket" Curse

Modern monetary system is brilliant invention, but has side effects that kill local life. First, money is centralized. It's born as debt to bank with interest. For money to exist in economy, someone must get into credit bondage. This creates eternal race: we must run faster just to pay interest.

Second, money behaves like water in leaky bucket. Imagine you spent 10 euros at local cafe. Seems you supported small business. But cafe owner will give part to bank for acquiring, part — to federal trade network for products, part — to landlord living in another country. Result: from your district this money evaporates almost instantly, washing value upward to global corporations and financial centers. Local communities are bled dry.

Finally, money is impersonal. It doesn't care who you are and what relationships you have. It doesn't remember good deeds and doesn't track reputation.

### Swiss Secret and Time as Currency

People aren't fools, and attempts to fix this system bug have been made for over hundred years.

Brightest example is perhaps Swiss WIR bank. In 1934, midst of Great Depression, when regular money physically didn't exist, Swiss entrepreneurs agreed: "Let's credit each other ourselves". They created WIR unit equal to franc and started mutual settlements. You give me timber, I give you hotel services. Regular money didn't participate. Almost hundred years passed, and WIR system still works, stabilizing Swiss economy in each crisis.

There were other attempts. LETS systems (Local Exchange Trading Systems), where neighbors record in notebook or Excel spreadsheet who owes whom how many "points" for household help. Or time banks where your time hour serves as currency, and lawyer's work hour equals nanny's work hour.

Idea is wonderful, but implementation always limped. Notebooks got lost, Excel tables became accountant's hell, and scaling such "interest club" to at least small city level was impossible. Enthusiasm faded facing accounting routine.

This is exactly where GEO enters stage.

### Not New Coin, But New Internet

We're not trying to create another cryptocurrency that needs to be "mined" or bought on exchange hoping to get rich. World has enough speculation.

GEO is protocol. Set of rules by which computers (and people) can agree on value. We're building what can be called "Internet of Value" for local communities. Idea is to take Swiss WIR or neighborly LETS mechanics and put it on modern, reliable IT architecture rails.

Imagine you come to community and instead of paying with familiar money, open **Trust Line**. This is system's key concept. Trust line isn't fund transfer, it's declaration: "I trust this person or business for up to 10,000 units". This is digital analog of "pencil" record in bar or neighborly agreement.

Second key entity is **Debt**. When you receive goods or service, you don't give coins. You simply use trust limit, and record appears in system: "I owe baker 200 units".

Most interesting starts when you want to pay someone you're not personally familiar with. Say you want to buy milk from farmer, but he doesn't know you and hasn't opened trust line to you. But he trusts local store owner, and store owner trusts you. GEO automatically finds this path. You become indebted to store owner, and store owner becomes indebted to farmer. Farmer got his obligation from trusted person, you got milk. Everyone happy, real money wasn't needed. System turned social capital (connections) into liquidity.

### Magic of Debt Disappearance

But GEO's main feature isn't even payments, but what economists call clearing. This resembles magic or "Tetris" game where filled lines disappear.

Imagine triangle: Alice owes Bob 100 euros, Bob owes Charlie 100 euros, and Charlie owes Alice 100 euros. In regular economy all three will be nervous, seek cash, run around borrowing to pay off. In GEO system algorithm sees this closed cycle. It understands: essentially nobody owes anybody anything. System simply takes and cancels these debts. Click — and obligations disappeared, balances evened out, tension gone.

In real economy such chains can be long and tangled. GEO finds and "collapses" them automatically. This frees colossal amount of resources. Business needs less working capital, people need less cash. Economy starts breathing freer.

### History Lessons: Why "Old" GEO Didn't Take Off

Here worth making small historical digression. We're not first to tackle this concept. Several years ago talented GEO Protocol team existed. They had global vision: create worldwide decentralized network uniting all blockchains and banking systems into single "internet of money".

They did huge theoretical work. They invented cool routing algorithms and concept of those same trust lines. But, as often happens with visionaries, they tried building starship when people needed bicycle. Their protocol was complex, abstract and required high qualification for understanding. Eventually, under market and investor pressure, project veered off path of creating new economy and became regular crypto exchange. "Credit network for people" idea was shelved.

We dusted off that shelf. We looked at their blueprints and decided: let's make it simpler. No need to immediately build "world brain". Let's make tool that can be launched in specific cooperative, coworking or settlement tomorrow.

### What We Are Building Now

We have designed **GEO v0.1** — a lightweight and pragmatic version of the protocol. We decisively cut away everything that hindered the launch: cumbersome global consensus mechanisms, the need for a single blockchain, and excessive technical abstractions.

The heart of the new architecture is the **Community-hub**. This is a node that serves one specific community. It maintains the trust graph, calculates routes for transactions, and finds closed debt loops for their automatic settlement (clearing).

At the start, this is indeed a centralized service — it is more reliable and faster for launching an MVP. However, the architecture is designed so that **control remains with the users**. All significant actions — whether opening a credit line or confirming a payment — are signed with private cryptographic keys directly on the participants' devices. The server here plays the role of an honest secretary and an efficient calculator, but by no means the owner of your assets.

In the future, hubs of independent communities will be able to establish connections with each other, forming a global distributed network. A district cooperative will be able to interact with a city time bank, and that — with a professional union of freelancers. But our path begins with something small — a working tool for local groups.

We already have detailed specifications ready: transaction formats, atomic change protocols (two-phase commit), and algorithms for finding clearing cycles. We have defined the technology stack — these are reliable, time-tested open-source tools.

### We Need Your Brains and Hands

Now we're transitioning from documents to code, and this is that very moment when project can become reality — or remain beautiful idea in Google Docs.

We need people. And not just "viewers", but participants.

If you're **developer** (especially if you're friendly with Python or React), here's virgin field for you. No boring tasks of shifting JSONs from one microservice to another for ad sales. Here need to solve graph theory problems, work with cryptography (Ed25519), build reliable distributed systems. This is real engineering challenge.

If you're **economist** or simply understand well how money works (and doesn't work), we need your experience. We're building model, and important not to step on rakes that LETS creators and other alternative currency makers already walked over.

If you're **community leader**, cooperative organizer or simply active person seeing your environment suffocating without resources — you're our potential pilot user. We're making this for you.

Ultimately, GEO is story about freedom. Freedom to choose ourselves what to consider value. Freedom to trust neighbor, not bank's scoring balance. Freedom to create economy with own hands.

Internet of information we already built. Seems time has come to build internet of trust. Let's do this together.
