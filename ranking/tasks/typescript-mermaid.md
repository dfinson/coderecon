# Tasks — mermaid-js/mermaid

8 tasks (3 narrow, 3 medium, 2 wide) for the TypeScript diagram generation engine.

## Narrow

### N1: Fix flowchart subgraph title clipping with long text

When a flowchart subgraph has a long title, the title text overflows the
subgraph boundary box and overlaps with nodes outside the subgraph. The
SVG text element is not clipped to the subgraph dimensions. Fix the
subgraph renderer to either truncate the title with an ellipsis or expand
the subgraph box to fit the title.

### N2: Add `direction` support to sequence diagrams

Sequence diagrams currently always flow top-to-bottom. Add support for
a `direction LR` directive that renders the sequence diagram left-to-right,
with participants stacked vertically and messages flowing horizontally.
This requires changes to the sequence diagram parser and renderer.

### N3: Fix class diagram relationship label positioning

In class diagrams, relationship labels (on association lines) overlap
with the line itself when the line is short or nearly vertical. The
label positioning algorithm does not account for line angle. Fix the
label placement to offset labels perpendicular to the line direction
so they are always readable.

## Medium

### M1: Implement Gantt chart critical path highlighting

Add automatic critical path detection and highlighting to Gantt charts.
The critical path (longest dependency chain determining project duration)
should be calculated from task dependencies and highlighted with a
distinct style (bold border or different color). Add a `criticalPath`
theme configuration option. Include tests for various dependency graph
topologies.

### M2: Add interactive click handlers to diagram elements

Implement a click handler system that allows diagram authors to attach
click callbacks or navigation URLs to diagram elements. Support
`click nodeId callback "tooltip"` syntax across flowcharts, class
diagrams, and state diagrams. The SVG output should include proper
cursor styling and ARIA attributes. Sanitize URLs to prevent XSS.

### M3: Implement diagram diff visualization

Add a "diff mode" that takes two versions of the same diagram definition
and renders a single diagram showing additions (green), deletions (red),
and modifications (yellow). This requires parsing both versions, computing
a structural diff of the diagram elements, and rendering the merged
result with diff annotations.

## Wide

### W1: Add accessibility support across all diagram types

Implement comprehensive accessibility for all diagram types. Generate
ARIA labels for all SVG elements, add a text-based alternative description
auto-generated from the diagram definition, support keyboard navigation
between diagram elements, and add screen reader announcements for
interactive elements. Each diagram type needs its own accessibility
strategy based on its semantic content.

### W2: Implement a diagram type plugin system

Refactor the diagram type registration to support third-party diagram
types as plugins. Extract the common diagram lifecycle (parse → layout →
render → interact) into a plugin API. Each diagram type should register
its parser grammar, renderer, theme handler, and accessibility provider
through the plugin interface. Migrate at least two existing diagram
types (pie, git) to use the plugin API as proof of the pattern.
