# Three-position enforcement — 2026-08-07

The bot now supports exactly three visible positions:

1. `SA`
2. `Commando`
3. `SA Team Leader`

`SA Water` and `SA Specialist Outlets` are legacy position values and are
automatically migrated to `SA` in both `sa_list` and `sa_users` at startup.
They are also rejected by `/add` and `/change position`.

The previous `sa_water: 12` validation output was an internal schedule key,
not a position count. It represented twelve people working 06:30–16:30. The
key is now named `sa_630`, and validation reports positions and schedules
separately:

```text
Position counts: {'SA': 208, 'Commando': 9, 'SA Team Leader': 6}
Schedule counts: {'7:00am-5:00pm': 209, '6:30am-4:30pm': 12,
                  '9:00am-8:00pm': 1, '7:30am-5:00pm': 1}
```

Names imported from the official workbook may still contain historical words
such as “SA Water”; that text is part of the person's name. Their `position`
field and the position displayed at the beginning of every alert line are `SA`.
