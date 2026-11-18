const assert = require('node:assert/strict');
const stats = require('../web/simulation_stats.js');

function testBattleSummarizesWinsLossesAndWinRates() {
  const result = stats.summarize({
    players_in_seat_order: ['Heroes', 'Villains'],
    attempted_games: 3,
    completed_games: 2,
    game_results: [
      'Game Result: Game 1 status=NORMAL_WIN durationMs=100 winner=Heroes',
      'Game Result: Game 2 status=NORMAL_WIN durationMs=120 winner=Villains',
      'Game Result: Game 3 status=TIMEOUT durationMs=300',
    ],
  });

  assert.equal(result.attempted, 3);
  assert.equal(result.completed, 2);
  assert.equal(result.unresolved, 0);
  assert.deepEqual(result.players, [
    {name: 'Heroes', wins: 1, losses: 1, draws: 0, winRate: 50},
    {name: 'Villains', wins: 1, losses: 1, draws: 0, winRate: 50},
  ]);
}

function testCommanderStatsIncludeAllPodPlayers() {
  const result = stats.summarize({
    players_in_seat_order: ['A', 'B', 'C', 'D'],
    attempted_games: 4,
    completed_games: 4,
    game_results: [
      'Game Result: Game 1 status=NORMAL_WIN winner=A',
      'Game Result: Game 2 status=NORMAL_WIN winner=C',
      'Game Result: Game 3 status=NORMAL_WIN winner=A',
      'Game Result: Game 4 status=NORMAL_WIN winner=D',
    ],
  });

  assert.deepEqual(result.players.map(player => [player.name, player.wins, player.losses, player.winRate]), [
    ['A', 2, 2, 50], ['B', 0, 4, 0], ['C', 1, 3, 25], ['D', 1, 3, 25],
  ]);
}

function testDrawsAndUnrecognizedWinnersStayOutOfWinRate() {
  const result = stats.summarize({
    players_in_seat_order: ['A', 'B'],
    attempted_games: 3,
    completed_games: 3,
    game_results: [
      'Game Result: Game 1 status=NORMAL_WIN winner=A',
      'Game Result: Game 2 status=NORMAL_DRAW',
      'Game Result: Game 3 status=NORMAL_WIN winner=Unknown Deck',
    ],
  });

  assert.equal(result.unresolved, 1);
  assert.deepEqual(result.players, [
    {name: 'A', wins: 1, losses: 0, draws: 1, winRate: 33.3},
    {name: 'B', wins: 0, losses: 1, draws: 1, winRate: 0},
  ]);
}

function testEmptyOrFailedJobsDoNotReportFalseLosses() {
  const result = stats.summarize({
    players_in_seat_order: ['A', 'B'],
    attempted_games: 2,
    completed_games: 0,
    game_results: [],
  });

  assert.equal(result.unresolved, 0);
  assert.deepEqual(result.players.map(player => [player.wins, player.losses, player.winRate]), [
    [0, 0, null], [0, 0, null],
  ]);
}

function testForgeAiWinnerLineResolvesDeckAfterSeatPrefix() {
  const result = stats.summarize({
    players_in_seat_order: ['Standard Marvel heroes lifegain counters 1', 'Standard Marvel villains graveyard counters 1'],
    attempted_games: 1,
    completed_games: 1,
    game_results: [
      'Game Result: Game 1 ended in 105324 ms. Ai(1)-Standard Marvel heroes lifegain counters 1 has won!',
    ],
  });

  assert.equal(result.unresolved, 0);
  assert.deepEqual(result.players.map(player => [player.name, player.wins, player.losses, player.winRate]), [
    ['Standard Marvel heroes lifegain counters 1', 1, 0, 100],
    ['Standard Marvel villains graveyard counters 1', 0, 1, 0],
  ]);
}

testBattleSummarizesWinsLossesAndWinRates();
testCommanderStatsIncludeAllPodPlayers();
testDrawsAndUnrecognizedWinnersStayOutOfWinRate();
testEmptyOrFailedJobsDoNotReportFalseLosses();
testForgeAiWinnerLineResolvesDeckAfterSeatPrefix();
process.stdout.write('5 simulation statistics tests passed.\n');
