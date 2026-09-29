from __future__ import annotations

import pytest


@pytest.fixture
def sample_email_text() -> str:
    return """Electric Arc Furnace

Comments

Run Information
User Id | lsh05222@yu.ac.kr
Date | 21/09/2026 22:09:36
Status | 0
Score | $ 419.22

Simulation settings
User Level | University Student
Steel Grade | Construction Steel

Cost Breakdown
Time (in minutes) | 65
Tapping mass | 88
Tap temperature | 1631
Total Energy | 36069 kWh
Total Energy | 411 kWh/t
Power | $ 173
Scrap | $ 65
Additions | $ 15980
Other consumables | $ 20559.41
Total Cost | $ 36792
Cost Per Tonne | $ 419.22

Steel Composition / wt%
Element | Current | Min | Max
C | 0.076 | 0.100 | 0.120
Si | 0.104 | 0.100 | 0.300
Mn | 0.667 | 1.000 | 1.500
P | 0.004 | 0.000 | 0.020
S | 0.009 | 0.000 | 0.030
Cr | 0.070 | 0.000 | 0.100
Mo | 0.030 | 0.000 | 0.040
Ni | 0.051 | 0.000 | 0.150
Cu | 0.085 | 0.000 | 0.150
N | 0.000 | 0.000 | 0.005
Nb | 0.000 | 0.000 | 0.050
Ti | 0.001 | 0.000 | 0.010

Additions
Ferro-Silicon 75, high purity | 0
Ferro-Silicon 75 | 0
Silico-Chromium | 0
Silico-Carbon | 0
Chrome-Carbure | 0
Chrome-Carbure Low S | 0
Ferro-Molybdenum | 0
Ferro-Vanadium | 0
Aluminum pebbles | 0
Carbon | 0
Iron Oxide | 250
Fluorspar | 0
Dolomite | 450
Lime | 700
High C Ferro-Manganese | 0
Low C Ferro-Manganese | 0

Slag Composition
Element | Current | Min | Max
Al2O3 | 7.875 | | 
CaO | 35.212 | | 
Cr2O3 | 1.110 | | 
FeO | 5.746 | 10 | 50
MgO | 7.021 | 8 | 12
MnO | 1.499 | | 
SiO2 | 41.324 | | 
P | 0.192 | | 
S | 0.016 | | 
Basicity | 0.852081661692 | 1.5 | 2.5

Event Log
[
"00:00:00,Selected%20user%20level::%20undefined",
"00:00:00,Selected%20steel%20grade::%20Construction%20Steel",
"00:00:29,Simulation%20rate%20changed:%2022",
"00:01:10,Scrap%20basket%20added%20with:%20No2%20Heavy:%201t;%20No1%20Bundles:%2046t",
"00:01:56,Power%20set%20to:%20120%20MW",
"00:16:13,Additions:%20Dolomite:%20450%20kg;%20Iron%20Oxide:%20250%20kg;%20Lime:%20700%20kg",
"00:25:12,Power%20set%20to:%200%20MW",
"00:29:33,Scrap%20basket%20added%20with:%20No1%20Bundles:%2041t",
"00:36:49,Power%20set%20to:%20105%20MW",
"00:55:52,Power%20set%20to:%200%20MW",
"00:58:05,Analysis%20Requested",
"01:01:05,Analysis%20received",
"01:03:55,Tapping%20start",
"01:05:48,Tapping%20complete"
]
"""


@pytest.fixture
def sample_email_html() -> str:
    return """
    <html><body>
      <h2>Run Information</h2>
      <table><tr><td>User Id</td><td>lsh05222@yu.ac.kr</td></tr>
      <tr><td>Date</td><td>21/09/2026 22:09:36</td></tr>
      <tr><td>Status</td><td>0</td></tr><tr><td>Score</td><td>$ 419.22</td></tr></table>
      <h2>Simulation settings</h2>
      <table><tr><td>User Level</td><td>University Student</td></tr>
      <tr><td>Steel Grade</td><td>Construction Steel</td></tr></table>
      <h2>Cost Breakdown</h2>
      <table><tr><td>Time (in minutes)</td><td>65</td></tr>
      <tr><td>Tap temperature</td><td>1631</td></tr>
      <tr><td>Total Energy</td><td>36069 kWh</td></tr>
      <tr><td>Total Energy</td><td>411 kWh/t</td></tr>
      <tr><td>Cost Per Tonne</td><td>$ 419.22</td></tr></table>
      <h2>Steel Composition / wt%</h2>
      <table><tr><th>Element</th><th>Current</th><th>Min</th><th>Max</th></tr>
      <tr><td>C</td><td>0.076</td><td>0.100</td><td>0.120</td></tr></table>
      <h2>Additions</h2>
      <table><tr><td>Iron Oxide</td><td>250</td></tr>
      <tr><td>Dolomite</td><td>450</td></tr><tr><td>Lime</td><td>700</td></tr></table>
      <h2>Slag Composition</h2>
      <table><tr><th>Element</th><th>Current</th><th>Min</th><th>Max</th></tr>
      <tr><td>Basicity</td><td>0.852</td><td>1.5</td><td>2.5</td></tr></table>
      <h2>Event Log</h2>
      <pre>["00:00:00,Power%20set%20to:%20120%20MW",
      "01:05:48,Tapping%20complete"]</pre>
    </body></html>
    """
