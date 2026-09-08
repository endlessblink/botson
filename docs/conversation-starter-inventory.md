# Conversation starter source inventory

Status: in_progress. Local source snapshot, 2026-09-06, before cleanup behavior edits. This is an archive of retired candidates, never positive prompt guidance. Production findings are inherited and require refresh. Only this inventory document was written by its inventory worker.

## Coverage and interpretation

Searched config, bot, dashboard (including templates), and scripts for pool filenames/loaders, daily_prompts, seed/reset callers, random selection, examples, fallback branches, weekday framing, and generic Hebrew question fragments. Read the pool files in full and inspected their direct consumers and planner render/fill/digest branches. No credentials, environment files, authorization files, private transcripts, or live feedback bodies were read. This inventory is complete for the two named static pools; it does not claim every Hebrew literal in every unrelated activity is defective.

The removal boundary is all reusable morning/evening and discussion pool entries, even where an individual topic-specific question could be acceptable as fresh reviewed content. Counts: morning 3, evening 5; discussions 279 in 12 categories. Preserve category keys/routing. Category sizes: art=25, support=15, cute=25, funny=25, gaming=25, fitness=15, general=24, movies=25, music=25, politics=25, singles=25, vegan=25.

## Active and hidden consumers

| Source | Consumer / behavior | Classification |
|---|---|---|
| prompts.yaml | bot/utils/config.py:get_prompts → bot/main.py startup → Database.seed_prompts | Active seed source |
| daily_prompts | db.py seed_prompts seeds only if whole table empty; get_random_prompt resets exhausted type and marks selected entry used | Persisted duplicate; YAML removal alone cannot retire existing rows |
| Both YAML pools | materializer.py:materialize_forward supplies examples to generate_slot_text; random samples and copy.materializer.prompt make them positive inspiration | Active generation influence |
| Both YAML pools | dashboard _sample_pool_examples feeds generation modes | Active positive few-shot |
| Both YAML pools / daily_prompts | dashboard send_prompt_now consumes daily_prompts for morning/evening and random discussions for discussion before send | Direct static delivery |
| discussions.yaml | legacy weekplan ai-fill retry failure chooses random pool item, source=ai-fill-pool (~5480) | Hidden static fallback |
| discussions.yaml | /api/weekplan/discussion-sample returns first category item | Hidden sendable preview |
| Both YAML pools | /weekplan builds morning_queue/evening_queue and category previews; pool_idx allows editing; committed rows override previews | Hidden reusable UI content, not merely decorative copy |
| Both YAML pools | prompts editor and weekplan pool update endpoints write files (~740,11626,11644) | Resurrection/edit path |
| discussions.yaml | _load_active_discussion_categories, suggest and digest category context | Preserve keys/configured routing; empty keys differ from missing categories |
| Both YAML pools | _build_activities_context counts, QA endpoint source example sets (~10616), cleanup_stale_scheduled_content.py | Inventory/quality inputs; not direct sends |
| scheduled_messages | calendar check_and_send_due_messages sends stored rows; new generation does not rewrite old text | Persisted delivery source; includes authored/copied/recurring rows, not just auto |
| weekday_rubrics.yaml | bot/utils/time_context.format_time_context; copy.time_context weekday header says framing mandatory | Forced reflection/task/week framing |
| settings.yaml copy.materializer | kind_morning=פתיחת יום; kind_evening=סגירת יום; positive examples block | Framing + positive injection template |
| dashboard digest prompt (~8298–8368) | Weekday prescriptions, positive Sunday task example and 15 canned formats | Hardcoded positive generation influence |
| dashboard condensed digest prompt (~8533) | Sunday morning is declared weekend-summary time | Hardcoded reflection framing |
| recent_sent_samples_by_type, this_week_previews | Digest asks to learn rhythm/style from recent sends; existing rows used for dedup elsewhere | Persisted influence, not evidence of approval |
| content_feedback + recent feedback cache | Dashboard accepted examples and rejected examples injected (~3900–3905) | Bot-owned dynamic positive/negative anchors; current contents uninspected |
| config/operator_prefs.md → prefs_store → data/operator_prefs.md | Baseline reconciliation and tombstones; operator_anchors parses Good/Bad examples | Persisted learning authority; baseline Good/Bad sections currently empty, live unverified |
| channel_rubrics.yaml | support asks small wins/compliments, singles daily-alone experiences, funny invites funny observations despite preference disabling funny questions | Topic framing, not stock sendable text; review conflict without erasing protections |

## Exact morning/evening entries (8)

```yaml
# Morning prompts (08:00 Israel time)
morning:
  - "בוקר טוב 🌞 מה הדבר הקטן שאם תסיימו היום יפנה לכם אוויר בראש?"
  - "☀️ מה אתם מורידים מהצלחת היום בכוונה, כדי לא להפוך את היום למרדף?"
  - "בוקר! ☕ איזו משימה אחת שווה לקבל היום את האנרגיה הראשונה שלכם?"

# Evening prompts (21:00 Israel time)
evening:
  - "ערב טוב 🌙 איזה דבר קטן סגרתם היום ועשה יותר שקט ממה שציפיתם?"
  - "🌙 מה נשאר פתוח היום, אבל אתם בוחרים לא לקחת איתכם ללילה?"
  - "ערב! ✨ רגע אחד מהיום שהזכיר לכם שאתם לא רק רשימת משימות?"
  - "🌙 מה עשיתם היום שהיה בשבילכם, לא בשביל להספיק?"
  - "✨ אם היום היה נסגר בשורה אחת ביומן פרטי — מה הייתם כותבים?"
```

## Exact discussion entries (279)

The following archive preserves every category and item in source order. These are removal candidates as reusable pools, not examples to feed back into the model.

```yaml
art:
- יצירה אחת שראיתם השבוע וגרמה לכם לעצור רגע — מה היא הייתה?
- כלי יצירה קטן ששינה לכם את העבודה יותר ממה שציפיתם?
- תמונה אחת מהשולחן שלכם עכשיו — מה הכי מספר שם על הראש שלכם? 🎨
- אמן/ית שאתם חוזרים אליהם כשנגמרת לכם השראה?
- דעה לא פופולרית על מוזיאונים או גלריות שתגנו עליה?
- אם הייתם צריכים ליצור חודש רק בשחור-לבן או רק בצבע אחד — מה בוחרים?
- פרויקט שהתחיל כקשקוש והפך למשהו רציני?
- טיפ אחד ליציאה מבלוק יצירתי שעבד לכם באמת?
- יצירה שלכם שעדיין לא הראיתם לאף אחד — מה עוצר אתכם?
- מה המדיום שנראה לכם הכי מפחיד להתחיל בו?
- ספר/סרט/אלבום שנתן לכם חשק ליצור משהו משלכם?
- תערוכה או גלריה ששווה להגיע אליה גם לבד?
- מה הדבר הכי מוזר ששמרתם כי "אולי זה יהיה חומר ליצירה"?
- יצירה מושלמת מבחינתכם = טכניקה חזקה + רעיון טוב + מה עוד?
- אם הייתם מעצבים פוסטר לקבוצה הערב — מה היה מופיע בו?
- הצבע שאתם משתמשים בו יותר מדי בלי לשים לב?
- מישהו נתן לכם ביקורת ששינתה את הדרך שבה אתם יוצרים?
- יצירה שנראתה לכם פשוטה עד שניסיתם לעשות משהו דומה?
- "מה עדיף לכם: לסיים מהר ולהמשיך הלאה, או ללטש עד שכבר נמאס?"
- מקום בעיר שנותן לכם חשק לצלם/לצייר/לכתוב?
- מה הדבר הכי קטן ביצירה שיכול להרוס לכם את כל החוויה?
- אם היה לכם יום חופשי רק ליצירה — איפה הייתם מתחילים?
- תראו לנו פרט קטן מיצירה שלכם, בלי לחשוף את הכול 👀
- מה למדתם לבד כי אף קורס לא הסביר את זה טוב?
- איזה סגנון אמנותי אתם לא מבינים עד הסוף אבל כן נמשכים אליו?
support:
- ניצחון קטן מהשבוע שמגיע לו רגע במה?
- משהו שמישהו עשה בשבילכם לאחרונה ועדיין נשאר איתכם?
- מחמאה שקיבלתם פעם ולקח לכם זמן להאמין לה?
- למי בקבוצה הייתם רוצים לפרגן היום, ועל מה?
- רגע קטן שבו הרגשתם שאתם בכיוון הנכון?
- מה הדבר הכי טוב שמישהו יכול להגיד לכם ביום קשה?
- "מה עדיף לכם לקבל: עצה פרקטית או פשוט שמישהו יהיה איתכם בזה?"
- פעולה קטנה שמרימה לכם מצב רוח בלי לעשות מזה אירוע?
- משהו שהייתם רוצים לחגוג למרות שהוא נראה קטן מבחוץ?
- מי נתן לכם דוגמה טובה לאחרונה בלי להתכוון?
- משפט עידוד שלא נשמע כמו קלישאה ועובד עליכם באמת?
- דבר אחד שאתם עושים טוב יותר ממה שאתם נותנים לעצמכם קרדיט?
- איפה אתם צריכים היום יותר פרגון ופחות פתרונות?
- משהו שהצליח לכם לאחרונה אחרי הרבה ניסיונות?
- מחווה קטנה בין חברים שמבחינתכם שווה הרבה?
cute:
- תמונת חמידות אחרונה ששמרתם בטלפון — מי מקבל את הבמה? 📸
- חיה שנראית כאילו היא יודעת סוד גדול מדי?
- מה הדבר הכי חמוד שראיתם השבוע מחוץ למסך?
- כלב, חתול או חיה שלישית שתמיד מנצחת אצלכם?
- סרטון חמוד ששלחתם למישהו בלי הסבר?
- אם חיית המחמד שלכם הייתה מנהלת את היום שלכם — מה היה קורה ראשון?
- פרווה, נוצות או קשקשים — מה הכי עובד עליכם?
- תמונה אחת של חיה עם הבעת פנים דרמטית מדי — תפילו 👇
- "דעה לא פופולרית על חמידות: איזו חיה מקבלת יותר מדי יחסי ציבור?"
- מקום שראיתם בו חיה והרגיש כמו סצנה מסרט?
- מה הצליל הכי חמוד שחיה יכולה לעשות?
- חיה קטנה שהייתם נותנים לה לנהל מדינה ליום אחד?
- מה הדבר הכי מצחיק שחיית מחמד עשתה לידכם?
- אם הייתם צריכים לבחור קמע לקבוצה — איזו חיה זו הייתה?
- תמונת "לפני קפה" של חיה — יש לכם אחת?
- איזה בעל חיים נראה תמיד כאילו איחר לפגישה?
- מה החיה הכי לא מוערכת מבחינת חמידות?
- חיה מצוירת אחת שעדיין עובדת עליכם רגשית?
- אם הייתם יכולים להבין חיה אחת ל-10 דקות — מי ולמה דווקא היא?
- איזה שם הכי מוגזם הייתם נותנים לחתול קטן?
- דבר חמוד שראיתם בטבע ולא הספקתם לצלם?
- "מה עדיף: חיה שנרדמת מוזר או חיה שמתעוררת מבולבלת?"
- תמונת החיה הכי "זה אני ביום ראשון" שיש לכם?
- איזו חיה נראית הכי מנומסת בלי סיבה?
- רגע קטן של חמידות שהציל לכם יום גרוע?
funny:
- תיקון אוטומטי אחד שהרס לכם הודעה — מה יצא?
- המם האחרון ששמרתם כי הוא היה מדויק מדי?
- אם החיים שלכם היו סיטקום, איך היו קוראים לפרק של השבוע?
- דבר קטן שגורם לכם לצחוק כל פעם מחדש?
- דעה לא פופולרית על קומדיות ישראליות שתגנו עליה?
- רגע שבו ניסיתם להיות רציניים ויצא לכם להפוך לבדיחה?
- מה המשפט הכי ישראלי ששמעתם בתור, במשרד או ברחוב?
- סרטון קצר שהצחיק אתכם בלי שתבינו למה?
- אם הייתם צריכים להמציא חוק מטופש לקבוצה ליום אחד — מה החוק?
- הבדיחה הכי גרועה שאתם עדיין מספרים בגאווה?
- מה הדבר הכי אבסורדי שראיתם השבוע ולא היה בפיד?
- איזה ביטוי אתם אומרים יותר מדי בלי לשים לב?
- כישרון מגוחך שיש לכם ולא נכנס לקורות חיים?
- "מה יותר מצחיק: כישלון במטבח או כישלון טכנולוגי?"
- אם הטלפון שלכם היה עושה לכם סטנדאפ — על מה הוא היה יורד?
- רגע "רק בישראל" שראיתם לאחרונה?
- איזה חפץ בבית שלכם נראה כאילו הוא שופט אתכם?
- מה הדבר הכי קטן שיכול להפוך אתכם לליצנים תוך שנייה?
- פאנץ' אחד שאתם לא מאמינים שעבד עליכם?
- אם הייתם מדרגים מבוכה מ-1 עד "נבלעתי באדמה" — מה הסיפור?
- איזו מילה בעברית תמיד נשמעת לכם מצחיקה?
- תמונה בלי הקשר שמצחיקה אתכם — תנו אחת.
- מה הדבר הכי מוזר שאמרתם בקול ואז הבנתם שיש אנשים סביבכם?
- דמות קומית אחת שהייתם לוקחים איתכם לסידורים?
- מה מצחיק אתכם גם כשאתם מנסים לא לצחוק?
gaming:
- דעה לא פופולרית על משחק AAA שתגנו עליה?
- אם הייתם יכולים לשחק רק משחק אחד שנה שלמה — מי שורד?
- "דרגו: סיפור, משחקיות, מוזיקה — מה הכי חשוב לכם במשחק?"
- משחק שכולם אהבו ואתם פשוט לא הצלחתם להיכנס אליו?
- PC או קונסולה — והשנה זה עדיין משנה לכם?
- משחק אינדי אחד שהייתם נותנים למישהו שלא משחק הרבה?
- הבוס הכי זכור לכם, לטוב או לרע?
- משחק מהילדות שעדיין נראה לכם קסום?
- סטאפ הגיימינג שלכם עכשיו — נקי, כאוס או מקדש? 🎮
- טיפ של ותיקים ששחקן מתחיל חייב לדעת?
- הסשן המושלם = משחק טוב + שתייה + מה עוד?
- המשחק האחרון שגרם לכם להגיד "רק עוד חמש דקות" ולשקר לעצמכם?
- "מה עדיף: עולם פתוח ענק או משחק קצר ומהודק?"
- משחק לוח שיכול למשוך גם אנשים שלא משחקים בדרך כלל?
- פסקול משחק שאתם שומעים גם בלי לשחק?
- החלטה במשחק שעדיין יושבת לכם בראש?
- איזה ז'אנר ניסיתם לאהוב ולא עבד?
- אם הייתם מוחקים זיכרון ממשחק אחד כדי לשחק שוב לראשונה — איזה?
- משחק שהביקורות פספסו לדעתכם?
- דמות משנה שהייתה צריכה לקבל משחק משלה?
- מה הדבר הכי מעצבן במשחקים מודרניים?
- קווסט צדדי שגנב את ההצגה מהעלילה הראשית?
- "מה יותר חשוב לכם: אתגר קשוח או זרימה רגועה?"
- המשחק שהכי מתאים לערב גשם לבד?
- איזה טריילר גרם לכם להאמין יותר מדי?
fitness:
- אימון קצר שבאמת עובד לכם גם ביום עמוס?
- מה הדבר הכי קטן שעוזר לכם לצאת לזוז כשאין חשק?
- "מה עדיף: עשר דקות כל יום או אימון ארוך פעם בשבוע?"
- תרגיל אחד שאתם שונאים אבל מודים שהוא עושה עבודה?
- שיר או פלייליסט שמצליח להרים אימון?
- "מה יותר קשה לכם: להתחיל, להתמיד, או לא להגזים?"
- טיפ התאוששות קטן שלמדתם בדרך הקשה?
- ציוד כושר שקניתם ובאמת נשאר בשימוש?
- יעד כושר קטן שמרגיש מציאותי לשבוע הקרוב?
- הליכה, חדר כושר או אימון ביתי — מה מחזיק אצלכם לאורך זמן?
- מה עוזר לכם לחזור לשגרה אחרי שבוע שהתפספס?
- ארוחה או נשנוש שעובדים לכם לפני או אחרי אימון?
- מדד התקדמות שלא קשור למשקל ושווה לעקוב אחריו?
- דבר אחד שהייתם אומרים למישהו שמתחיל לזוז מחדש?
- מה הסימן שלכם שאימון היה טוב, חוץ מלהיות גמורים?
general:
- דבר אחד שקניתם באונליין ועדיין מפתיע אתכם שהוא באמת שימושי?
- דעה לא פופולרית על חופשות שתגנו עליה?
- אם הייתם לומדים מיומנות אחת מחר בבוקר — מה הייתם בוחרים?
- מה הדבר שאנשים תמיד מופתעים לגלות עליכם?
- משפט אחד שמתאר את השבוע שלכם בלי להגיד "עמוס"?
- מה הדבר הכי טוב בלחיות בלי ילדים, דווקא ביום רגיל?
- מקום בעולם שהייתם עוברים אליו לחודש ניסיון?
- הרגל קטן שהפך לכם את החיים לקצת יותר קלים?
- מה הדבר הכי מוערך יתר על המידה לדעתכם?
- פודקאסט או ערוץ יוטיוב ששווה לפתוח ממנו פרק אחד?
- אם הייתם מרצים 20 דקות בלי הכנה — על איזה נושא?
- דבר אחד שעשיתם לבד וגיליתם שזה הרבה יותר כיף ככה?
- "מה עדיף: לתכנן הכול מראש או להשאיר מקום לאלתור?"
- חפץ בבית שמספר עליכם יותר מדי?
- החלטה קטנה מהשנה האחרונה שהתבררה כגדולה?
- מה הדבר שאתם לא עושים יותר, וטוב שכך?
- איזה כלל חברתי הייתם מבטלים לשבוע?
- דבר אחד שהייתם רוצים שיותר אנשים יבינו על חיים ללא ילדים?
- מה הדבר הכי טוב שלמדתם מהאינטרנט ולא מבית ספר?
- אם היה לכם בוקר חופשי בלי התחייבויות — איפה הייתם מתחילים?
- מה הדבר שהייתם עושים יותר אם לא הייתם צריכים להסביר אותו לאף אחד?
- ספר, סרט או שיחה ששינו לכם זווית לאחרונה?
- מה הדבר הכי קטן שגורם לבית להרגיש כמו בית?
- איזה ויכוח טיפשי אתם מוכנים לנהל עד הסוף?
movies:
- סרט שראיתם בקולנוע בלי לדעת עליו כלום ויצאתם מופתעים?
- דעה לא פופולרית על סרט שכולם אוהבים?
- סדרה שהייתה צריכה להסתיים עונה אחת קודם?
- "דרגו: תסריט, משחק, צילום — מה מציל סרט בינוני?"
- סרט שמתאים לראות לבד בלילה שקט?
- דוקו אחד שגרם לכם לפתוח עוד שלושה טאבים אחריו?
- סצנה אחת שאתם זוכרים יותר מהסרט כולו?
- נטפליקס, דיסני+ או ספרייה פיראטית בראש — איפה אתם באמת מוצאים דברים?
- סרט ישראלי שהייתם נותנים למישהו שלא רואה קולנוע ישראלי?
- סדרה שביטלתם אחרי פרק אחד בצדק?
- שחקן/ית שאתם מוכנים לראות כמעט בכל דבר?
- "מה עדיף: סוף פתוח או סגירה מוחלטת?"
- סרט ילדות ששרד צפייה חוזרת כמבוגרים?
- פסקול סרט שנשאר איתכם יותר מהעלילה?
- סרט רע שאתם אוהבים בלי להתנצל?
- אם הייתם מוחקים זיכרון מסדרה אחת כדי לראות מחדש — איזו?
- ז'אנר שאתם חוזרים אליו כשאין כוח לבחור?
- טוויסט קולנועי שעדיין עובד גם כשכבר יודעים אותו?
- סרט שהטריילר עשה לו עוול?
- דמות משנה שגנבה את כל הסרט?
- מה הסרט הכי טוב שראיתם השנה עד עכשיו?
- סדרה שהתחילה חלש ואז פתאום תפסה אתכם?
- בינג' מושלם = פרקים קצרים, אוכל טוב ומה עוד?
- סרט שעדיף לראות עם עוד אנשים ולא לבד?
- המלצה אחת למישהו שאומר "אין כבר מה לראות"?
music:
- אלבום ששמעתם פעם אחת ואז נשאר לכם בראש שבוע?
- דעה לא פופולרית על אמן שכולם אוהבים ואתם פחות?
- שיר אחד שמתאים לנסיעה בלילה בלי לדבר?
- הופעה חיה שהייתם חוזרים אליה אם היה אפשר?
- "מה עדיף: אלבום מושלם מקצה לקצה או פלייליסט חד פעמי?"
- זמר/ת שהבנתם מאוחר מדי כמה הם טובים?
- שיר ילדות ששרד גם באוזניים של מבוגרים?
- פסקול סרט או משחק שאתם שומעים גם בלי הסרט או המשחק?
- כלי נגינה שהייתם רוצים לדעת לנגן בלי להתחיל מאפס?
- מה השיר האחרון שעצרתם כדי לבדוק מי מבצע אותו?
- אלבום שמתאים לשמוע לבד בבית ולא ברקע?
- שיר גרוע שאתם אוהבים בלי להתנצל?
- "דרגו: מילים, לחן, ביצוע — מה תופס אתכם ראשון?"
- אמן ישראלי שהייתם נותנים למישהו שלא שומע מוזיקה ישראלית?
- קאבר אחד שעשה לשיר יותר טוב מהמקור?
- שיר שכולם מכירים אבל אתם עדיין לא שבעים ממנו?
- מה הדבר הכי מעצבן בהופעות חיות?
- פלייליסט מושלם לערב שקט = גיטרות, פסנתר ומה עוד?
- ז'אנר שניסיתם לאהוב ולא הצלחתם?
- שיר שהפתיע אתכם כי הוא הגיע ממקום לא צפוי?
- אם הייתם מוחקים זיכרון מאלבום אחד כדי לשמוע מחדש — איזה?
- "מוזיקה לעבודה: מילים, בלי מילים או שקט מוחלט?"
- להקה שהייתה צריכה להוציא אלבום אחד פחות?
- שיר אחד שמרים לכם מצב רוח תוך 20 שניות?
- המלצה אחת למישהו שאומר "אין לי כוח לחפש מוזיקה חדשה"?
politics:
- דעה פוליטית ששיניתם עליה עמדה בשנים האחרונות?
- מקור אחד שעוזר לכם להבין נושא מורכב בלי לצעוק?
- החלטה ציבורית אחת שהייתם רוצים שמישהו יסביר בשפה פשוטה?
- מה הנושא שכולם מדברים עליו, אבל הפרט החשוב בו הולך לאיבוד?
- פודקאסט או ניוזלטר שמצליח להוריד רעש במקום להוסיף?
- איך אתם יודעים מתי להפסיק לקרוא חדשות ליום אחד?
- דילמה אזרחית אחת שאין לה תשובה קלה מבחינתכם?
- נאום, ראיון או משפט ציבורי שנשאר לכם בראש לאחרונה?
- "מה עדיף בדיון פוליטי: עובדות יבשות או סיפור אישי טוב?"
- נושא אחד שהייתם רוצים לראות עליו יותר נתונים ופחות סיסמאות?
- ספר שעזר לכם להבין מדינה, חברה או תקופה אחרת?
- מה הדבר הכי קטן שאזרח רגיל יכול לעשות ועדיין יש לו ערך?
- איפה עובר אצלכם הגבול בין להתעדכן לבין להישאב?
- דעה לא פופולרית על תקשורת שאתם מוכנים להגן עליה?
- אירוע היסטורי שאתם חושבים שמסביר משהו שקורה עכשיו?
- מה השאלה שהייתם שואלים פוליטיקאי אם היה חייב לענות קצר?
- איזה מושג פוליטי הייתם רוצים שיסבירו בלי סלוגנים?
- מתי בפעם האחרונה שיחה פוליטית דווקא שינתה משהו אצלכם?
- "מה עדיף: פשרה גרועה או עמידה עקרונית שמפסידה?"
- נושא מקומי קטן שמגיע לו יותר תשומת לב?
- איך אתם מזהים כותרת שנועדה רק להדליק אתכם?
- מאמר אחד שקראתם עד הסוף ולא רק כותרת?
- אם הייתם בונים שיעור אזרחות למבוגרים — במה הייתם פותחים?
- מה הדבר שהכי חסר לכם בשיח ציבורי היום?
- מקור מידע אחד שאתם סומכים עליו רק חלקית, ועדיין חוזרים אליו?
singles:
- הדבר הכי טוב שגיליתם על עצמכם בתקופה בלי זוגיות קבועה?
- "דייט ראשון מושלם מבחינתכם: קפה קצר או ערב מלא?"
- סימן אזהרה קטן בדייט שכבר למדתם לא להתעלם ממנו?
- אפליקציות דייטינג — כלי שימושי או עייפות מצטברת?
- סיפור דייט מוזר שאפשר לספר בלי לחשוף שמות?
- מה הדבר שהכי חשוב לכם בפרטנר, וגיליתם את זה מאוחר?
- ערב שישי לבד בבית — פינוק, שקט או פספוס?
- טיפ אחד לדייטינג שהייתם נותנים לעצמכם לפני חמש שנים?
- "מה עדיף: כימיה מיידית או שיחה שנבנית לאט?"
- משפט פתיחה שקיבל מכם תשובה דווקא כי היה פשוט?
- פעילות דייט ראשון שלא מרגישה כמו ראיון עבודה?
- מה למדתם להגיד "לא" עליו מהר יותר?
- יתרון אחד של חיים עצמאיים שאנשים בזוגיות לא תמיד מבינים?
- אם הייתם מוחקים חוק לא כתוב אחד בדייטינג — איזה?
- תמונת פרופיל שגורמת לכם להחליק שמאלה מיד?
- מה הדבר הכי טוב שקיבלתם מתקופה לבד?
- דייטים בבוקר או בערב — מה עובד לכם יותר?
- שאלה אחת שאתם אוהבים לשאול בתחילת היכרות?
- "מה יותר חשוב בהתחלה: צחוק משותף או קצב חיים דומה?"
- רגע שבו הבנתם שאתם נהנים מהחיים שלכם כמו שהם?
- איזה תחביב שלכם הפך למסנן טוב להיכרויות?
- מה הדבר שהכי מעייף אתכם בשיחות פתיחה?
- מחמאה אחת בדייט שנשמעה אמיתית ולא תסריט?
- אם הייתם כותבים ביוגרפיית אפליקציה בלי קלישאות — מה חייב להופיע?
- גבול אישי אחד שהתחלתם לשמור עליו טוב יותר?
vegan:
- מנה טבעונית שאנשים מופתעים ממנה בכל פעם?
- מסעדה טבעונית או צמחונית שאתם חוזרים אליה בלי לבדוק תפריט?
- טיפ אחד למי שרוצה לאכול יותר צמחי בלי להפוך את זה לפרויקט?
- מוצר טבעוני שהפתיע אתכם לטובה השנה?
- ארוחת בוקר טבעונית מהירה שבאמת מחזיקה?
- דעה לא פופולרית על תחליפי בשר שתגנו עליה?
- "מה עדיף: טופו קריספי או קטניות בתבשיל עמוק?"
- רוטב אחד שמציל כמעט כל מנה טבעונית?
- מקום שקשה בו להיות טבעוני אבל מצאתם בו פתרון?
- חטיף טבעוני שאתם קונים יותר ממה שתכננתם?
- מנה ביתית שלוקחת פחות מ-15 דקות ונראית מושקעת?
- ספר, דוקו או פודקאסט ששינה לכם משהו בצלחת?
- טיפ קניות טבעוני שחוסך כסף או כאב ראש?
- איזה ירק מקבל אצלכם יחס של כוכב ראשי?
- מה הדבר שהכי עזר לכם לא להפוך טבעונות לוויכוח בכל ארוחה?
- מנה טבעונית שהייתם מגישים למישהו סקפטי בלי נאום לפני?
- "מה עדיף לקינוח: שוקולד מריר טוב או משהו על בסיס קוקוס?"
- שילוב טעמים שנשמע מוזר ועובד לכם?
- מוצר מדף אחד שהפך את המטבח שלכם לקל יותר?
- אם הייתם בונים צלחת טבעונית מושלמת — מה המרכיב הראשון?
- מה אתם שמים בכריך כשאין כוח לבשל?
- מנה ממטבח עולמי שקל להפוך לטבעונית בלי להרוס אותה?
- טיפ אחד למסעדות כשאין כמעט אופציות בתפריט?
- מה הדבר הכי קטן שהפך בישול טבעוני לכיף אצלכם?
- ארוחה טבעונית מנחמת ליום עייף — מה נכנס לצלחת?
```

## Exact weekday framing (7)

```yaml
# Per-Hebrew-day tone/framing for discussion / morning / evening
# generation. Loaded by bot.utils.time_context.format_time_context()
# and appended to the time-context block so the LLM anchors on the
# right register for the day.
#
# Keys must match the day names in
# config/settings.yaml:copy.time_context.day_names.
#
# Empty list = no special framing for that day (regular weekday tone).

rubrics:
  שישי:
    - "סוף שבוע — שאלה אינטליגנטית לרפלקציה על השבוע שעבר. לא רשימת הישגים, אלא בחירה, החלטה, שינוי, רגע, או תובנה שמייצגים את השבוע. הימנעו מ-'איך היה השבוע' גנרי."
  שבת:
    - "סוף שבוע — מבט קדימה לשבוע הבא. שאלה על תכנון, ציפייה, החלטה שצריך לקבל, או דבר אחד שרוצים להתחיל / לשנות / לסיים בשבוע הקרוב."
  ראשון:
    - "פתיחת שבוע — שאלה על החלטה, מטלה, או דבר אחד שמתחילים השבוע. עוגן בסצנת התחלה קונקרטית (משימה, מייל, הרשמה, פגישה ראשונה). הימנעו מ-'מה התוכניות לשבוע' גנרי."
  שני:
    - "אמצע התחלת השבוע — שאלה על משהו שכבר הוחלט/קרה אתמול-היום, או דילמה שצריך לפתור עד אמצע השבוע. עוגן קונקרטי, לא 'איך הולך השבוע'."
  שלישי:
    - "מרכז השבוע — שאלה על הרגלים, שגרה, או רגע אמצע-שבוע ספציפי. הימנעו מ-'הגענו לאמצע השבוע' כפילר; בקשו פרט (קפה, מסלול, ארוחה, החלטה קטנה)."
  רביעי:
    - "לפני שבת — שאלה על משהו שרוצים לסגור עד סוף השבוע, או החלטה ספציפית (משלוח, ביקור, קנייה אחרונה, מסעדה). לא 'מה התכנון לשבת' פתוח."
  חמישי:
    - "ערב לפני סופ\"ש — שאלה על מעבר ממצב עבודה למצב חופש: רגע ספציפי, מטלה אחרונה, בחירה לערב. הימנעו מ-'איך נסגר השבוע' / 'הריטואל שסוגר' — דרשו עוגן קונקרטי."
```

## Additional hardcoded positive digest guidance

Source excerpts below are archived evidence, not approved examples. The quoted positive Sunday evening task question is especially similar to the reported harm. Adjacent rejected examples remain negative fixtures and must not be promoted.

```text
7. אל תחזור על נושאים ש-this_week_previews כבר מכסים. regular_slots חייבים להיות בזווית חדשה.

7a. **איכות תוכן — לא generic. חובה.** הקהילה היא "אלהוריים וזה" — מבוגרים צ'יילדפרי בעברית, בני 30-50, עם ערוצים על gaming, סרטים, אומנות, פוליטיקה, גיקים, בישול וכד'. הימנע מתבניות חלולות כמו "מה היה היום?" או "ספרו דבר טוב". במקום זאת:

   - **שלב את היום בשבוע — ובאופן מדויק לפי השעה. הקפד: בישראל שישי+שבת = סוף שבוע. שבוע עבודה מתחיל ראשון בבוקר.**
     - ראשון בוקר = חזרה לשגרה, אחרי סוף שבוע, מה התכניות?
     - שני-חמישי = אמצע שבוע, יום עבודה רגיל
     - שישי בוקר = סוף שבוע מתחיל היום, מה תכניות לסוף שבוע?
     - שישי ערב = הסוף שבוע כבר בעיצומו, איך זה הולך?
     - **שבת בוקר/צהריים = עדיין סוף שבוע, האווירה רגועה ופנויה. אסור לדבר על "סוף השבוע שעבר"!**
     - **שבת ערב (עד 22:00) = סוף שבוע עדיין נמשך, בסיום קל. הימנע מ"איך עבר סוף השבוע?" כי הוא לא עבר. אפשר "מה עוד עושים הערב?", "מה רואים הלילה?".**
     - שבת מאוחר (22:00+) או ראשון מוקדם = סיום סוף שבוע, מותר להזכיר "סיכום סוף השבוע".

   - **התמקד בעתיד, לא בעבר. חובה לפחות סלוט אחד הצעת פעולה אקטיבית, לא רק שאלה רפלקטיבית:**
     - אסור לתלות הכל בסשן/אירוע של "אמש" (זה משעמם וצופה אחורה).
     - חייב להציע משהו לעשות עכשיו/הערב/השבוע: "מי בעניין של משחק רוקטליג ב-22:00?", "ערב סדרה ביחד? מה רואים?", "פיצ'ר את ה-3 משחקי קופסה האהובים עליכם — נבחר משחק להפעיל".
     - ❌ "🌙 שני בלילה — שבוע חדש התחיל." — שגוי. שני זה אמצע שבוע.
     - ✅ "🌙 ראשון בערב — היום הראשון של השבוע נסגר. מה המשימה הכי חשובה השבוע?"

     a. **Hot take / דעה לא פופולרית** — "סרט שכולם אוהבים — ולא מבינים מה הם רואים?", "סדרה הכי overrated של 2025?"
     b. **Forced choice / scenario עם אילוץ** — "אם אתם יכולים לשחק רק משחק אחד עד סוף החיים — מי?", "סדרה אחת לעולם בודד?"
     c. **Mini-list (top 3 / ranking)** — "פיצ'ר 3 משחקי קופסה האהובים", "דרגו: Yellowstone/Suits/Friends — האהוב, הכי 'ברקע', הכי בינג'."
     d. **Recommendation request** — "מחפש פודקאסט על קולנוע — ממליצים?", "סוף שבוע גשום, סדרה חדשה לבינג', מה כדאי?"
     e. **Comparison / binary A-vs-B** — "PC או קונסולה ולמה?", "DC או Marvel?"
     f. **Specific memory / nostalgia anchor** — "הסרט הראשון שראיתם בקולנוע — זוכרים?", "אנימה ראשונה שהשתקעתם בה — איזו?"
     g. **Show-and-tell (image cue)** — "התמונה האחרונה במצלמה.", "צלם את הספר שעל השולחן עכשיו."
     h. **Insider knowledge / hack** — "טיפ של 5 שנים בגיימינג שכל מתחיל היה צריך לדעת?"
     i. **Fill-in-the-blank** — "ערב סוף שבוע מושלם = ___ + ___ + ___."
     j. **Web rabbit-hole** — "ירדתם השבוע ל-rabbit hole? איזה?", "wikipedia article אחת ששלחתם לחבר השבוע?"
     k. **Niche self-expression** — "על איזה נושא תוכלו להעביר הרצאת TED של 20 דקות בלי הכנה?"
     l. **Would-you-rather (dilemma)** — "תעדיפו לקרוא את כל הספרים שלא קראתם או לראות את כל הסרטים — בלי לישון?"
     m. **Pool the group (collective list)** — "בואו נכתוב יחד את 10 הסדרות שכל גיק חייב לראות — תכתבו אחת + שורה."
     n. **Frame-your-own-question (meta)** — "אם הייתם המנחים הערב — איזו שאלה הייתם שואלים?"
     o. **Childfree-specific** — "הדבר שאתם עושים עכשיו שלא הייתם עושים אם היו לכם ילדים?" (השתמש במידה — חשוב לקהילה אבל לא בכל פוסט)

     דוגמה: ❌ "סדרה שאתם מריצים שוב ושוב" → ✅ "Yellowstone/Suits/Friends — איזו הכי 'background friendly' ולמה?"

   - **למד מ-recent_sent_samples_by_type**: זה הסגנון של הקהילה. שכפל את הקצב, את האמוג'ים שעובדים, את אורך המשפט. אל תיצור משהו שלא יושב על הטון הזה.

- כבד verified_topic_ids בלבד. אל תנחש topic_id.
- אל תכפיל מול existing_drafts_today או scheduled_messages_today.
- מזג אירועים כפולים; reminder_scheduled_time הוא זמן האירוע פחות event_reminder_lead_minutes.
- אם היום שבת או שישי בערב: סוף השבוע עדיין קורה; אל תכתוב "איך היה"/"סיכום"/עבר. ראשון בבוקר הוא זמן סיכום סוף שבוע.
- פעילויות מותרות רק אם הבוט מפעיל אותן או שהן שאלה/פול קל. אסור להציע מפגש/משחק שדורש תיאום אדמין.
```

## Excluded classes and residual gaps

### Post-cleanup local audit (2026-09-07)

This section describes the current local implementation; archived entries above describe the removed sources. The follow-up `rg` search covered `bot`, `dashboard`, `scripts`, and `config` Python/YAML/HTML for `weekday_rubrics`, `channel_rubrics`, `daily_prompts`, `get_random_prompt`, `seed_prompts`, `prompts.yaml`, and `discussions.yaml`, with focused reads of generation, preference and preview consumers. It does not certify every possible generated phrase or deployed behavior.

- `prompts.yaml` and the twelve `discussions.yaml` lists are empty; category keys remain routing metadata. `seed_prompts` is a no-op; `get_random_prompt` returns empty and never resets historical rows. `_sample_pool_examples` returns empty. Materializer passes empty examples; its compatibility examples parameter is exclusion evidence only. The archived positive digest scripts and weekday thematic rubrics have been removed from their active prompt paths.
- Remaining pool references include the prompts editor and `/api/prompts/save`, legacy `/api/weekplan/update` pool writes, activities item counts, weekplan diagnostic logging, category discovery in materializer/Populate/digest, source-duplicate validation, and `scripts/validate_discussions.py` / `scripts/cleanup_stale_scheduled_content.py`. These still permit inspection or repopulation of YAML; they are not proof of a static send path. Weekplan morning/evening preview queues are explicitly empty. Comments still describing pools as optional few-shot/fallback material are stale documentation.
- `config/channel_rubrics.yaml` remains active via `_load_channel_rubric` in `_build_generate_prompt`. It contains eleven configured categories: movies, gaming, music, vegan, politics, art, support, fitness, funny, cute, singles. These are topical instructions, not full starter pools. Four lines were subsequently replaced with abstract concrete-choice guidance. Exact retired lines: “התמקדו בפרגון, חיזוק, הכרה בניצחונות קטנים, ועזרה רגשית קלה.”; “בקשו פרט אחד שאפשר לענות עליו בקצרה: מחמאה, ניצחון קטן, תמיכה שקיבלו.”; “השאלה צריכה להזמין חשיפה של רגע מצחיק או תצפית קטנה — לא 'מה הכי מצחיק'.”; “חוויות יומיום של חיים בגפם — בחירות קטנות, יתרונות, אתגרים, רגעי אושר.” Fitness still steers toward “הרגלים” and “שגרה”; cute still permits stories/documentation. Missing/mismatched topic categories suppress these rubrics. They can influence generation but are not proven provenance for row 780.
- `bot/utils/time_context.py` still loads weekday rubrics and supplies actual date/time context; retain factual calendar correctness separately from removed weekly reflection themes.
- Positive examples remain possible through the operator-owned preference layer: `### Good examples — Hebrew content` is read by `bot/utils/operator_anchors.py` and dashboard `_active_style_profile_block_sync`; materializer and activity-copy generation consume these anchors. The checked-in baseline currently has **zero bullets** under Good examples and Bad examples. Live `data/operator_prefs.md` may differ. `prefs_store.py` reconciles the Hebrew rules and both anchor sections from the baseline, respecting removal tombstones; editing empty YAML starter pools does not clear live anchors.
- The funny-channel rule previously included the positive illustration “ספרו רגע מצחיק מהבוקר”. The local baseline now replaces that rule with abstract guidance; the live copy still requires the exact authenticated removal documented in conversation-preference-repair-manifest.json. Reconciliation is append-only, so deployment alone cannot remove it. Preference rule and anchor readers remain intentionally active. Dashboard also falls back to `_STYLE_PROFILE_CACHE['planner_hebrew_default']` if the Hebrew rule section is empty.
- Dashboard `_recent_feedback_block_sync` still injects accepted/accepted-after-edit feedback as positive examples and rejected feedback as negative evidence. Digest context still collects bounded recent sent samples. These are dynamic historical influences; no raw feedback/private transcript corpus was inspected in this audit. Their existence means empty static pools alone cannot guarantee semantic freshness; the shared reviewer is the enforcement boundary.
- Production pending IDs **785, 786, 787, 792, 813, 814** were refreshed September 7. A separate read-only production inspection reverified all eight daily prompt rows (IDs 1–3 morning, 4–8 evening), exactly matching the eight archived originals above. Both live and baseline preference anchor sections contain zero Good and zero Bad example bullets. Local cleanup and inventory do not establish deployment, queue mutation, or Telegram-visible correction.

- config/freshness.yaml rejection fragments, config/question_quality.md rejected examples, dashboard validation regexes, config/discussion_pool_baseline.yaml grandfathered validator failures, tests and quality_feedback fixtures are negative evidence. Retain useful regressions outside active pools.
- config/facts.yaml, trivia.yaml, emoji pools, free-game metadata, event/RSVP copy, weekly summaries and leaderboards are curated activities or functional messages. Their full inventories are outside conversation-starter removal; preserve executable payload and preview/send parity.
- Functional dashboard labels, placeholders, help, welcomes, moderation, scoring, reminders and bot command responses are distinct. Hebrew-literal guardian failures are not by themselves proof of generic-starter content.
- Existing weekly-review handler/settings/dashboard work is unrelated dirty work and was not changed or treated as a new cleanup source.
- No independent morning/evening/discussion cron sender remains in handlers; jobs.py delegates text slots to materialization. bot/handlers/discussions.py contains category metadata. scripts/test_sync.py checks absence of removed cron functions; scripts/demo_calendar.py reads scheduled data rather than introducing its own starter pool.
- Sent row 780 remains inherited evidence; its exact sentence was found in scheduled data, not proven in Python/YAML. The old nine-row pending count is superseded by the six-row snapshot.
- Live preferences contain additional synthesized prohibitions through August 31; preserve them. Production tracked config is clean against its own d99b555 revision, not necessarily equal to this release. Persisted draft/job results and dynamic feedback influences remain unexamined hidden data sources; all resulting conversation prose must pass the send boundary. No private feedback/transcripts were inspected.
- Searches are bounded to tracked-like source directories and explicit relevant files; archives, dependencies, binary media, secrets, private transcripts and unrelated external workspaces were excluded. A follow-up search after cleanup must verify remaining references and distinguish harmless readers/negative evidence from active reusable starters.
