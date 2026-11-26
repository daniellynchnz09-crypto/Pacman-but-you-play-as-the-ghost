# Ghost vs Pac-Man

A Python implementation of a ghost-controlled Pac-Man game using Pygame.

## Game Description

In this version of Pac-Man, you play as the ghost trying to catch the AI-controlled Pac-Man. The goal is to catch Pac-Man before he clears the maze.

## Features Implemented

1. **Classic Maze Layout**: Fixed maze with proper walls and paths like classic Pac-Man
2. **Central Spawn Area**: Ghost spawns in a central box, Pac-Man spawns just outside
3. Multiple Ghosts: Four ghosts (Blinky, Pinky, Inky, Clyde) with different colors
4. Power Pellets: Special pellets that make ghosts edible for 5 seconds
5. Pathfinding AI: Ghosts use A* pathfinding to navigate the maze properly
6. Game Mechanics:
   - Score tracking
   - Timer (300 seconds)
   - Win/lose conditions
   - Ghost respawning

## Controls

- Arrow keys or WASD: Move your ghost
- Enter: Start game
- R: Restart game
- S (on the game-over screen): Save the replay
- M, or a click on the speaker button at the bottom right: Mute / unmute sound
- Q or Esc: Quit

## Sound

The game makes its own arcade-style effects (pellet chomps, the power-pellet
wobble, a ghost being eaten, the start, and a different ending for each
result). They are synthesised in code when the game starts - there are no
sound files. Set the environment variable `PACMAN_SOUND=0` to switch them off,
or `PACMAN_SOUND_VOLUME` (0 to 1, default 0.6) to change how loud they are.

## Pac-Man AI switches

Pac-Man's behaviour can be adjusted with environment variables set before
the game starts. None of them changes a rule of the game - only how he decides.

- `PACMAN_PLANNER` (default 1): the whole-game planner. Your ghost is faster
  than Pac-Man, so a ghost that simply runs him down always wins in the end
  unless he eats it: each power pellet, eaten with the ghost on his heels,
  sends it back to the house and buys him time. Once Pac-Man has seen your
  ghost make 40 moves in a row exactly as a plain chaser would
  (`PACMAN_PLANNER_TRUST_RUN`), he stops deciding one move at a time and
  plans the whole rest of the game - every pellet, with the four power
  pellets held back for the moments the ghost is close enough to be eaten.
  He checks the plan against the game at every move and plans again when a
  ghost does something he did not expect. One move a chaser would not make
  and he goes back to deciding move by move (and then needs 80 such moves
  before he believes it again), so against a ghost that cuts him off or
  seals him in he plays as he did before.
  The planning runs in a second process so the game does not stutter; if
  that process cannot start, he simply plays without it. Set
  `PACMAN_PLANNER=0` to switch the planner off.
  Measured in simulated games against a saved ghost that only chases
  (`claude_plus`), with Pac-Man not told how it moves: he cleared the maze
  in 78 of 208 games (37.5%), against 0 of the same 208 without the planner.
  Against the saved ghosts that play more like a person the planner stays
  off and the results are unchanged.
- `PACMAN_SURE_KILL` (default 1): the certain kill. While a power pellet is
  working, your ghost walks at about two thirds of his speed, so each step he
  takes towards it closes the gap whatever it does. When the power time left
  only just covers that gap he goes and eats it, as late as that is still
  certain so the pellets on the way are eaten first (`PACMAN_SURE_KILL_SLACK_MS`,
  default 1400). Eaten, your ghost sits in the house for 3 seconds. In 109
  power pellets he ate in your recorded games your ghost was eaten after 30
  of them; in simulation this rule takes that to about 70-75% and adds
  about 1 point of maze eaten against the stand-in for you and 2 against a
  sharper "pouncing" ghost (800 games each). It changes nothing about the
  rules of the game. Set `PACMAN_SURE_KILL=0` to switch it off.
- `PACMAN_HOLD_FACTOR` (default 2): the idle-ghost rule. When your ghost has
  stood on one tile although free to move, Pac-Man assumes it will stay there
  about twice as long again (at most 8 seconds) and plans around it. It applies
  after 0.6 seconds for a ghost that has not left the house and after 3 seconds
  for one standing still outside. Without it, a ghost that never moved won
  almost every game by stalemate. Set `PACMAN_HOLD_FACTOR=0` to switch it off.
- `PACMAN_HOLD_SHUFFLE` (default 1): a ghost stepping back and forth between
  two neighbouring tiles for 3 seconds counts as standing still for the rule
  above, and Pac-Man keeps off both tiles. Set `PACMAN_HOLD_SHUFFLE=0` to
  switch it off.

## Requirements

- Python 3.x
- Pygame (install with `pip install pygame`)

## How to Play

1. Run the game with `python Ghost_Pacman.py`
2. Press Enter or a movement key to start
3. Pac-Man gets a 3 second head start. Your ghost then walks out of the ghost
   house by itself, and from there you steer it with the arrow keys or WASD.
   A direction you press is remembered until the next turn where it is
   possible, so you can press it just before a corner.
4. Your four ally ghosts (Blinky, Pinky, Inky, Clyde) leave the house one at a
   time over the next few seconds and hunt Pac-Man on their own
5. Catch Pac-Man - touch him with your ghost, or help an ally ghost do it -
   before he eats every pellet
6. When Pac-Man eats a power pellet every ghost turns blue for 5 seconds:
   keep away from him until it wears off, or he eats your ghost and sends it
   back to the house
7. When the game ends, press R to play again or S to save the replay

## Game Rules

- If a ghost catches Pac-Man, the ghost team (you) wins
- If Pac-Man eats every pellet, Pac-Man wins - that is his only way to win
- There is no score and no clock to run down
- Eating a power pellet makes ghosts edible for 5 seconds
- An eaten ghost goes back to the ghost house and waits 3 seconds before it can leave again
- Backstop: a game that reaches 5 minutes with neither result ends as a stalemate, which counts as a ghost win

## Technical Details

- Maze layout uses classic Pac-Man design with proper walls
- Ghosts have their own AI with pathfinding capabilities
- All ghosts start in the center area (like classic game)
- No double lanes without walls like in original implementation
- Pathfinding algorithm prevents ghosts from getting stuck in corners
- Central spawn area with ghost in box, Pac-Man just outside

The game is now much closer to a classic Pac-Man experience while maintaining the unique twist of playing as the ghost.
