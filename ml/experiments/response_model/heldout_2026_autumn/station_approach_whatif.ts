// What reaches Olympic Park station under different assumptions about the walk there.
// Reads the demand packets in the folder given as the first argument and walks each one on
// copies of the network held in memory. No file under src/ is changed.
// Called by station_approach_whatif.py.
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { bundle } from '../../../../src/data/index';
import { simulate } from '../../../../src/engine/simulate';
import currentMapping from '../../../../src/data/egress-access.json';

type Network = typeof bundle.network;
const flow = bundle.assumptions.flowPerMeterPerMin;
// Width of stairs that passes the given number of people a minute under the network's own rule.
const stairs = (personsPerMin: number) => personsPerMin / (flow.flat * flow.stairsFactor);
const edge = (network: Network, id: string) => network.edges.find((e) => e.id === id)!;
const COUNTED = 290;   // people a minute the gates sustained (station_capacity.json)

const SETTINGS: [string, (network: Network) => void][] = [
  // The packet's five-minute departures walked at the network's speed with nothing in the way.
  ['walk_without_limits', (n) => { for (const e of n.edges) { e.widthM *= 100; e.queueAreaM2 *= 100; } }],
  ['network_as_it_is', () => {}],
  ['entrance_at_counted_throughput', (n) => { edge(n, 'e-station-a').widthM = stairs(COUNTED); }],
  ['and_room_at_the_station_front', (n) => { edge(n, 'e-station-a').widthM = stairs(COUNTED); edge(n, 'e-station-a').queueAreaM2 = 232; }],
  ['and_room_on_the_approach', (n) => {
    edge(n, 'e-station-a').widthM = stairs(COUNTED); edge(n, 'e-station-a').queueAreaM2 = 232; edge(n, 'e-04').queueAreaM2 = 290;
  }],
];
const folder = process.argv[2]!;
const rows: unknown[] = [];
for (const name of readdirSync(folder).filter((n) => n.endsWith('.json')).sort()) {
  const packet = JSON.parse(readFileSync(join(folder, name), 'utf8'));
  for (const [setting, change] of SETTINGS) {
    const network = structuredClone(bundle.network);
    change(network);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const result: any = (simulate as any)({ demandPacket: packet, network, routes: bundle.routes,
      assumptions: bundle.assumptions, mapping: currentMapping, tailMinutes: 180 });
    const edges = Object.entries(result.edges) as [string, { peakQueuePersons: number; waitingMinuteCount: number; queuePersonMinutes: number | string }][];
    const longest = edges.reduce((best, item) => (item[1].peakQueuePersons > best[1].peakQueuePersons ? item : best), edges[0]!);
    rows.push({ date: name.slice(0, 10), setting, start: result.start,
      arrivals: result.byDestination['n-dest-a']?.arrivalsAtMinute ?? {},
      largestQueuePersons: longest[1].peakQueuePersons, largestQueueLink: longest[0], minutesWithAQueueThere: longest[1].waitingMinuteCount,
      queuePersonMinutes: edges.reduce((n, item) => n + Number(item[1].queuePersonMinutes), 0) });
  }
}
process.stdout.write(JSON.stringify(rows));
