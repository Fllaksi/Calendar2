#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, messagebox, colorchooser
from datetime import date, datetime, timedelta
import calendar, traceback
from decimal import Decimal
import os
import sqlite3

from .constants import cents_to_money, format_minutes_hhmm
from . import database, calculations, events, widgets
from .profile_manager import ProfileManager, parse_hhmm_to_min, format_min_to_hhmm

def center_window(window, width=None, height=None):
    window.update_idletasks()
    if width is None:
        width = window.winfo_width()
    if height is None:
        height = window.winfo_height()
    sw = window.winfo_screenwidth()
    sh = window.winfo_screenheight()
    x = (sw - width) // 2
    y = (sh - height) // 2
    window.geometry(f"{width}x{height}+{x}+{y}")

class CalendarApp:
    def __init__(self, master, profile_name, manager: ProfileManager):
        self.master = master
        self.master.title(f"Salary Calendar (Рабочий календарь) - {profile_name}")
        self.master.geometry("1150x740")
        center_window(self.master, 1150, 740)  # ← добавить эту строку
        self.master.resizable(False, False)
        self.profile_name = profile_name
        self.manager = manager
        self.db_path = os.path.join(manager.profiles_dir, f"{profile_name}.db")
        self.conn = sqlite3.connect(self.db_path)
        if not self._db_exists():
            database.init_db(self.conn)
            self.manager.save_default_colors(self.conn)
        self.base_amount = Decimal(self.manager.load_setting(self.conn, 'salary', '90610.5'))
        self.lunch_min = int(self.manager.load_setting(self.conn, 'lunch_min', '60'))
        self.required_minutes = 480 + self.lunch_min  # 8h + lunch
        self.colors = self.manager.load_colors(self.conn)
        self.holidays_set, self.holidays_names = self._load_manual_holidays(range(2024,2028))
        self.today = date.today(); self.cur_year = self.today.year; self.cur_month = self.today.month
        self.tooltip = None
        self.after_id = None
        self._build_ui(); self._draw_calendar(); self._start_timer()

    def _start_shift_today(self):
        if self.today.month != self.cur_month or self.today.year != self.cur_year:
            messagebox.showinfo("Инфо", "Кнопки работают только для текущего дня в текущем месяце")
            return
        now = datetime.now().strftime("%H:%M")
        day_iso = self.today.isoformat()
        shift = database.load_shift(self.conn, day_iso)
        if shift and shift[0]:  # Уже есть активация
            messagebox.showinfo("Инфо", "Смена уже начата")
            return
        # Сохраняем только активацию
        database.save_shift(self.conn, day_iso, now, None, None, 0, 0, 0, 0, "")
        self._draw_calendar()

    def _end_shift_today(self):
        if self.today.month != self.cur_month or self.today.year != self.cur_year:
            messagebox.showinfo("Инфо", "Кнопки работают только для текущего дня")
            return
        now = datetime.now().strftime("%H:%M")
        day_iso = self.today.isoformat()
        shift = database.load_shift(self.conn, day_iso)
        if not shift or not shift[0]:
            messagebox.showinfo("Инфо", "Смена не начата")
            return
        if shift[1]:  # Уже есть конец
            messagebox.showinfo("Инфо", "Смена уже закончена")
            return
        self._on_day_click(self.today)  # Открываем редактирование, чтобы пользователь подтвердил/изменил
        activation = shift[0]
        database.save_shift(self.conn, day_iso, activation, now, None, 0, 0, 0, 0, shift[7] or "")
        self._draw_calendar()

    def _distribute_overtime(self):
        # Простая реализация: распределить все доступные переработки за текущий месяц
        pending = database.find_pending_overtimes(self.conn, self.cur_year, self.cur_month)
        for day_iso, ot_min in pending:
            if ot_min > 0:
                events.distribute_overtime_minutes(self.conn, self.cur_year, self.cur_month,
                                                   1 if day_iso <= f"{self.cur_year}-{self.cur_month:02d}-15" else 2,
                                                   day_iso, ot_min)
        self._draw_calendar()
        messagebox.showinfo("Готово", "Переработки обработаны")

    def _db_exists(self):
        cur = self.conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = cur.fetchall()
        return bool(tables)

    def _load_manual_holidays(self, years):
        hset = set(); names = {}
        for y in years:
            for mday in range(1,10):
                from datetime import date as _d
                hset.add(_d(y,1,mday)); names[_d(y,1,mday)]="Новогодние каникулы"
            from datetime import date as _d
            names[_d(y,1,7)]="Рождество"; hset.add(_d(y,1,7))
            names[_d(y,2,23)]="День защитника Отечества"; hset.add(_d(y,2,23))
            names[_d(y,3,8)]="Международный женский день"; hset.add(_d(y,3,8))
            names[_d(y,5,1)]="Праздник труда"; hset.add(_d(y,5,1))
            names[_d(y,5,9)]="День Победы"; hset.add(_d(y,5,9))
            names[_d(y,6,12)]="День России"; hset.add(_d(y,6,12))
            names[_d(y,11,4)]="День единства"; hset.add(_d(y,11,4))
            names[_d(y, 12, 31)] = "Новый год"; hset.add(_d(y, 12, 31))
        return hset, names

    def _build_ui(self):
        top = ttk.Frame(self.master)
        top.pack(fill="x", padx=8, pady=6)

        # Кнопки переключения месяца слева
        ttk.Button(top, text="◀", width=3, command=self._prev_month).pack(side="left", padx=5)
        ttk.Button(top, text="▶", width=3, command=self._next_month).pack(side="left", padx=5)

        # Название месяца по центру (создаём и pack'им здесь!)
        self.lbl_month = ttk.Label(top, text="", font=("Segoe UI", 14, "bold"))
        self.lbl_month.pack(side="left", expand=True, padx=20)  # expand=True центрирует его

        # Кнопки профиля и настроек справа
        self.btn_profile = ttk.Button(top, text="👤", width=3, command=self._on_profile)
        self.btn_profile.pack(side="right", padx=5)
        self.create_tooltip(self.btn_profile, "Профиль")

        self.btn_settings = ttk.Button(top, text="⚙", width=3, command=self._on_settings)
        self.btn_settings.pack(side="right", padx=5)
        self.create_tooltip(self.btn_settings, "Настройки вида")
        # Панель статуса сверху по центру
        # Две отдельные рамки сверху по центру
        status_container = ttk.Frame(self.master)
        status_container.pack(fill="x", pady=10)

        # Рамка для ожидаемого конца
        end_frame = ttk.LabelFrame(status_container, text=" Ожидаемый конец смены ", padding=10)
        end_frame.pack(side="left", expand=True, fill="x", padx=20)
        self.lbl_expected_end = ttk.Label(end_frame, text="—", font=("Segoe UI", 12))
        self.lbl_expected_end.pack()

        # Рамка для заработка сегодня
        earn_frame = ttk.LabelFrame(status_container, text=" Заработок за сегодня ", padding=10)
        earn_frame.pack(side="left", expand=True, fill="x", padx=20)
        self.lbl_today_earn = ttk.Label(earn_frame, text="0.00 руб", font=("Segoe UI", 12))
        self.lbl_today_earn.pack()
        nav = ttk.Frame(self.master); nav.pack(fill="x", padx=8)
        ttk.Label(nav, text="Год:").pack(side="left")
        self.spin_year = tk.Spinbox(nav, from_=1970, to=2100, width=6, command=self._on_spin)
        self.spin_year.delete(0,"end"); self.spin_year.insert(0,str(self.cur_year)); self.spin_year.pack(side="left", padx=(6,12))
        ttk.Label(nav, text="Месяц:").pack(side="left")
        self.cmb_month = ttk.Combobox(nav, values=[calendar.month_name[i] for i in range(1,13)], state="readonly", width=18)
        self.cmb_month.current(self.cur_month-1); self.cmb_month.bind("<<ComboboxSelected>>", self._on_combo)
        self.cmb_month.pack(side="left", padx=6)
        self.cal_frame = ttk.Frame(self.master); self.cal_frame.pack(padx=8, pady=6, fill="both", expand=True)
        self.day_buttons = {}
        self._create_calendar_grid()
        self.info_frame = ttk.Frame(self.master)
        self.info_frame.pack(side="bottom", fill="x", pady=10)

        # Правый нижний угол — зарплата
        salary_frame = ttk.Frame(self.info_frame)
        salary_frame.pack(side="right", padx=20)

        ttk.Label(salary_frame, text="Зарплата 14 числа (за 16-30(31) предыдущего месяца):",
                  font=("Segoe UI", 10)).grid(row=0, column=0, sticky="e")
        self.lbl_salary_second_prev = ttk.Label(salary_frame, text="", font=("Segoe UI", 10))
        self.lbl_salary_second_prev.grid(row=0, column=1, padx=10)

        ttk.Label(salary_frame, text="Зарплата 29 числа (за 1-15 текущего месяца):", font=("Segoe UI", 10)).grid(row=1,
                                                                                                                 column=0,
                                                                                                                 sticky="e")
        self.lbl_salary_first = ttk.Label(salary_frame, text="", font=("Segoe UI", 10))
        self.lbl_salary_first.grid(row=1, column=1, padx=10)

        # Остальные метки (переработка и т.д.) слева внизу
        left_info = ttk.Frame(self.info_frame)
        left_info.pack(side="left", padx=20)
        self.lbl_pending_overtime = ttk.Label(left_info, text="")
        self.lbl_pending_overtime.pack()

        buttons_frame = ttk.Frame(self.master)
        buttons_frame.pack(side="bottom", fill="x", pady=5)

        ttk.Button(buttons_frame, text="Начать смену", command=self._start_shift_today).pack(side="left", padx=15)
        ttk.Button(buttons_frame, text="Закончить смену", command=self._end_shift_today).pack(side="left", padx=15)
        ttk.Button(buttons_frame, text="Обработать переработки", command=self._distribute_overtime).pack(side="left",
                                                                                                         padx=15)

    def create_tooltip(self, widget, text):
        def enter(event):
            x = widget.winfo_rootx() + 25
            y = widget.winfo_rooty() + 25
            self.tw = tk.Toplevel(widget)
            self.tw.wm_overrideredirect(True)
            self.tw.wm_geometry(f"+{x}+{y}")
            label = tk.Label(self.tw, text=text, background="yellow", relief="solid", borderwidth=1)
            label.pack()
        def leave(event):
            if hasattr(self, 'tw'):
                self.tw.destroy()
        widget.bind("<Enter>", enter)
        widget.bind("<Leave>", leave)

    def _on_profile(self):
        popup = tk.Menu(self.master, tearoff=0)
        popup.add_command(label="Сменить профиль", command=self._change_profile)
        popup.add_command(label="Изменить данные Профиля", command=self._edit_profile)
        popup.add_command(label="Выйти из профиля", command=self._logout)
        x = self.btn_profile.winfo_rootx()
        y = self.btn_profile.winfo_rooty() + self.btn_profile.winfo_height()
        popup.tk_popup(x, y, 0)

    def _change_profile(self):
        if self.after_id is not None:
            self.master.after_cancel(self.after_id)
            self.after_id = None
        self.master.destroy()

    def _edit_profile(self):
        dlg = tk.Toplevel(self.master)
        dlg.title("Изменить данные профиля")
        dlg.resizable(False, False)
        dlg.geometry("400x400")  # ← можно задать размер
        center_window(dlg, 400, 400)  # ← центрируем
        current_salary = self.base_amount
        current_lunch = self.lunch_min
        current_name = self.profile_name
        ttk.Label(dlg, text="Фамилия и Имя:").grid(row=0, column=0, sticky="w", padx=5, pady=2)
        ent_name = ttk.Entry(dlg); ent_name.insert(0, current_name); ent_name.grid(row=0, column=1)
        ttk.Label(dlg, text="Зарплата:").grid(row=1, column=0, sticky="w", padx=5, pady=2)
        ent_salary = ttk.Entry(dlg); ent_salary.insert(0, str(current_salary)); ent_salary.grid(row=1, column=1)
        ttk.Label(dlg, text="Время обеда (HH:MM):").grid(row=2, column=0, sticky="w", padx=5, pady=2)
        ent_lunch = ttk.Entry(dlg); ent_lunch.insert(0, format_min_to_hhmm(current_lunch)); ent_lunch.grid(row=2, column=1)
        ttk.Label(dlg, text="Новый Пин-Код (оставьте пустым если не менять):").grid(row=3, column=0, sticky="w", padx=5, pady=2)
        ent_pin = ttk.Entry(dlg, show="*"); ent_pin.grid(row=3, column=1)
        ttk.Label(dlg, text="Повторите Пин-Код:").grid(row=4, column=0, sticky="w", padx=5, pady=2)
        ent_repeat = ttk.Entry(dlg, show="*"); ent_repeat.grid(row=4, column=1)
        def on_save():
            new_name = ent_name.get().strip()
            profiles = self.manager.get_profiles()
            if new_name != current_name and new_name in profiles:
                messagebox.showerror("Ошибка", "Имя существует")
                return
            try:
                salary = Decimal(ent_salary.get().strip())
            except:
                messagebox.showerror("Ошибка", "Неверная зарплата")
                return
            lunch_str = ent_lunch.get().strip()
            try:
                lunch_min = parse_hhmm_to_min(lunch_str)
            except:
                messagebox.showerror("Ошибка", "Время обеда HH:MM")
                return
            pin = ent_pin.get()
            repeat = ent_repeat.get()
            if pin and pin != repeat:
                messagebox.showerror("Ошибка", "Пины не совпадают")
                return
            if pin and not pin.isdigit():
                messagebox.showerror("Ошибка", "Пин цифры")
                return
            self.manager.save_setting(self.conn, 'salary', str(salary))
            self.manager.save_setting(self.conn, 'lunch_min', str(lunch_min))
            self.base_amount = salary
            self.lunch_min = lunch_min
            self.required_minutes = 480 + lunch_min
            if pin:
                self.manager.pins[new_name] = pin
            if new_name != current_name:
                old_db = self.db_path
                new_db = os.path.join(self.manager.profiles_dir, f"{new_name}.db")
                self.conn.close()
                os.rename(old_db, new_db)
                self.db_path = new_db
                self.conn = sqlite3.connect(new_db)
                if current_name in self.manager.pins:
                    self.manager.pins[new_name] = self.manager.pins.pop(current_name)
                self.profile_name = new_name
                self.master.title(f"Salary Calendar (Рабочий календарь) - {new_name}")
            self.manager.save_pins()
            messagebox.showinfo("Успех", "Данные обновлены")
            dlg.destroy()
            self._draw_calendar()  # Refresh if needed
        ttk.Button(dlg, text="Сохранить", command=on_save).grid(row=5, column=0, columnspan=2, pady=5)
        dlg.grab_set()
        self.master.wait_window(dlg)

    def _on_settings(self):
        dlg = tk.Toplevel(self.master)
        dlg.title("Настройки Вида")
        dlg.resizable(False, False)
        dlg.geometry("500x600")
        center_window(dlg, 500, 600)
        colors = self.colors.copy()
        entries = {}
        row = 0
        for k in sorted(colors):
            rus_names = {
                "gold": "Дни с переработкой, учтённой в зарплате",
                "other_month": "Дни другого месяца",
                "weekday_ok": "Обычный рабочий день",
                "future_current_month": "Будущие дни текущего месяца",
                "past_no_data": "Прошлый день без записи",
                "today": "Сегодняшний день",
                "weekend": "Выходной или праздник",
                "undertime": "День с недоработкой",
                "header_bg": "Фон заголовков и недели",
                "weekly_overtime": "Неделя с переработкой",
                "weekly_undertime": "Неделя с недоработкой",
            }

            row = 0
            for k in sorted(colors):
                rus_text = rus_names.get(k, k)  # если вдруг добавится новый цвет — покажем английское имя
                ttk.Label(dlg, text=rus_text).grid(row=row, column=0, sticky="w", padx=10, pady=8)
                ent = ttk.Entry(dlg, width=15)
                ent.insert(0, colors[k])
                ent.grid(row=row, column=1, padx=10, pady=8)
                entries[k] = ent

                def choose(color_key=k, entry=ent):
                    col = colorchooser.askcolor(color=colors[color_key])[1]
                    if col:
                        entry.delete(0, tk.END)
                        entry.insert(0, col)

                ttk.Button(dlg, text="Выбрать цвет", command=choose).grid(row=row, column=2, padx=10, pady=8)
                row += 1
        def on_save():
            for k, ent in entries.items():
                color = ent.get().strip()
                if color and len(color) == 7 and color.startswith('#'):  # Basic hex validation
                    self.manager.save_setting(self.conn, f"color_{k}", color)
            self.colors = self.manager.load_colors(self.conn)
            self._draw_calendar()
            dlg.destroy()
        ttk.Button(dlg, text="Сохранить", command=on_save).grid(row=row, column=0, columnspan=3, pady=5)
        dlg.grab_set()
        self.master.wait_window(dlg)

    def _logout(self):
        if self.after_id is not None:
            self.master.after_cancel(self.after_id)
            self.after_id = None
        self.master.destroy()

    def _prev_month(self):
        if self.cur_month == 1:
            self.cur_month = 12; self.cur_year -= 1
        else:
            self.cur_month -= 1
        self._draw_calendar()

    def _next_month(self):
        if self.cur_month == 12:
            self.cur_month = 1; self.cur_year += 1
        else:
            self.cur_month += 1
        self._draw_calendar()

    def _on_spin(self):
        try:
            self.cur_year = int(self.spin_year.get())
            self._draw_calendar()
        except:
            pass

    def _on_combo(self, event):
        self.cur_month = self.cmb_month.current() + 1
        self._draw_calendar()

    def _create_calendar_grid(self):
        # Настраиваем растягивание клеток
        for i in range(9):  # столбцы 0-8 (дни 1-7, неделя 8)
            self.cal_frame.grid_columnconfigure(i, weight=1)
        for i in range(7):  # строки 0-6
            self.cal_frame.grid_rowconfigure(i, weight=1)

        # Заголовки дней недели (Пн-Вс в столбцах 1-7)
        days_headers = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
        for c, txt in enumerate(days_headers):
            lbl = tk.Label(
                self.cal_frame,
                text=txt,
                bg=self.colors["header_bg"],
                relief="ridge",
                anchor="center",
                font=("Segoe UI", 10, "bold")
            )
            lbl.grid(row=0, column=c + 1, sticky="nsew")

        # Заголовок "Неделя" справа
        week_header = tk.Label(
            self.cal_frame,
            text="Неделя",
            bg=self.colors["header_bg"],
            relief="ridge",
            anchor="center",
            font=("Segoe UI", 10, "bold")
        )
        week_header.grid(row=0, column=8, sticky="nsew")

        # Кнопки дней и метки недель
        for r in range(1, 7):
            # Метка недели справа (столбец 8)
            week_lbl = tk.Label(
                self.cal_frame,
                text="",
                bg=self.colors["header_bg"],
                relief="ridge",
                anchor="center",
                font=("Segoe UI", 10)
            )
            week_lbl.grid(row=r, column=8, sticky="nsew")

            # Кнопки дней (столбцы 1-7)
            for c in range(1, 8):
                btn = tk.Button(
                    self.cal_frame,
                    text="",
                    width=12,
                    height=6,  # ← увеличил высоту
                    relief="flat",
                    command=lambda row=r, col=c: self._on_day_click(self.day_buttons[(row, col)]["date"])
                )
                btn.grid(row=r, column=c, sticky="nsew")
                self.day_buttons[(r, c)] = {"btn": btn, "date": None}  # ← исправлено: было c+1
                btn.bind("<Enter>", lambda e, rc=(r, c): self._show_tooltip(e, rc))  # ← исправлено: было c+1
                btn.bind("<Leave>", lambda e: self._hide_tooltip())


    def _draw_calendar(self):
        self.lbl_month.config(text=f"{calendar.month_name[self.cur_month]} {self.cur_year}")
        self.spin_year.delete(0, "end")
        self.spin_year.insert(0, str(self.cur_year))
        self.cmb_month.current(self.cur_month - 1)

        cal = calendar.Calendar()
        weeks = cal.monthdatescalendar(self.cur_year, self.cur_month)
        num_weeks = len(weeks)  # 4, 5 или 6

        for r in range(1, 7):
            if r > num_weeks:  # скрываем лишние строки
                for c in range(9):
                    widget = self.cal_frame.grid_slaves(row=r, column=c)
                    if widget:
                        widget[0].grid_remove()  # скрываем
                continue

        for r in range(1, 7):
            weekly_total_min = 0
            for c in range(1, 8):
                btn_dict = self.day_buttons[(r, c)]
                btn = btn_dict["btn"]

                # Если неделя закончилась — отключаем кнопку
                if r - 1 >= len(weeks):
                    btn.config(
                        text="",
                        state="disabled",
                        bg=self.colors["other_month"]  # серый фон для дней вне месяца
                    )
                    continue

                d = weeks[r - 1][c - 1]
                btn_dict["date"] = d

                # Определяем цвет дня
                color = self._color_for_day(d)

                # Применяем настройки кнопки
                btn.config(
                    text=str(d.day),
                    state="normal",
                    bg=color,
                    fg="black",  # цвет текста (можно менять)
                    relief="flat"  # плоский вид, как у ttk
                )

                # Считаем время за неделю
                # Считаем время за неделю (без обеда)
                shift = database.load_shift(self.conn, d.isoformat())
                if shift:
                    work_min = shift[2] or 0
                    if work_min > 480:  # если больше 8 часов — вычитаем обед
                        work_min -= self.lunch_min
                    weekly_total_min += work_min

            # Обновляем метку недели справа
            week_lbl = self.cal_frame.grid_slaves(row=r, column=8)[0]
            if weekly_total_min > 0:
                week_color = (self.colors["weekly_overtime"] if weekly_total_min > 5 * 480 else
                              self.colors["weekly_undertime"] if weekly_total_min < 5 * 480 else
                              self.colors["header_bg"])
                week_lbl.config(bg=week_color, text=format_minutes_hhmm(weekly_total_min))
            else:
                week_lbl.config(bg=self.colors["header_bg"], text="")

        self._update_info_labels()

    def _color_for_day(self, d):
        if d.month != self.cur_month:
            return self.colors["other_month"]
        if d > self.today and d.month == self.cur_month:
            return self.colors["future_current_month"]  # новый цвет
        if d == self.today:
            return self.colors["today"]
        if d < self.today:
            return self.colors["past_no_data"] if not shift else (
                self.colors["undertime"] if (shift[3] or 0) > 0 else self.colors["weekday_ok"] if not is_weekend else
                self.colors["weekend"])
        # Будущие дни
        if shift:
            return self.colors["undertime"] if (shift[3] or 0) > 0 else self.colors["weekday_ok"] if not is_weekend else \
            self.colors["weekend"]
        return self.colors["weekday_ok"] if not is_weekend else self.colors["weekend"]

    def _show_tooltip(self, event, rc):
        d = self.day_buttons[rc]["date"]
        if not d: return
        shift = database.load_shift(self.conn, d.isoformat())
        lines = self._tooltip_lines_for_day(d, shift)
        if not lines: return
        self.tooltip = widgets.Tooltip(self.master, lines, lambda: self._on_day_click(d))
        x, y = event.x_root + 10, event.y_root + 10
        self.tooltip.show_at(x, y)

    def _hide_tooltip(self):
        if self.tooltip: self.tooltip.close(); self.tooltip = None

    def _tooltip_lines_for_day(self, d, shift):
        lines = [d.strftime("%d %B %Y")]
        if d in self.holidays_names:
            lines.append(self.holidays_names[d])
        if shift:
            act = shift[0] or "Нет"
            end = shift[1] or "Нет"
            duration = format_minutes_hhmm(shift[2] or 0)
            undertime = format_minutes_hhmm(shift[3] or 0)
            overtime = format_minutes_hhmm(shift[4] or 0)
            day_pay = cents_to_money(shift[5] or 0)  # ← исправлено: было 6
            ot_pay = cents_to_money(shift[6] or 0)  # ← исправлено: было 7
            notes = shift[7] or "Нет заметок"  # ← исправлено: было 8
            lines += [
                f"Активация: {act}",
                f"Окончание: {end}",
                f"Длительность: {duration}",
                f"Недоработка: {undertime}",
                f"Переработка: {overtime}",
                f"Оплата дня: {day_pay} руб",
                f"Оплата ОТ: {ot_pay} руб",
                f"Заметки: {notes[:50]}..." if len(notes or "") > 50 else f"Заметки: {notes}"
            ]
        return lines

    def _on_day_click(self, d):
        if self.tooltip: self.tooltip.close()
        existing = database.load_shift(self.conn, d.isoformat()) or {}
        existing_dict = {"activation": existing[0], "end": existing[1], "notes": existing[7]} if existing else {}
        dlg = widgets.EditShiftDialog(self.master, d, existing_dict, self.conn, self.lunch_min)
        self.master.wait_window(dlg)
        if not dlg.result: return
        if dlg.result.get("deleted"):
            self._draw_calendar()
            return
        activation = dlg.result["activation"]
        end = dlg.result["end"]
        notes = dlg.result["notes"]
        duration_min = self._calculate_duration(activation, end)
        hourly = calculations.hourly_rate_for_month(d.year, d.month, self.holidays_set, self.base_amount)
        is_weekend = d.weekday() >= 5 or d in self.holidays_set
        if not is_weekend:
            undertime_min = max(0, self.required_minutes - duration_min)
            overtime_min = max(0, duration_min - self.required_minutes)
            day_pay_cents = calculations.day_base_pay(hourly)
            overtime_pay_cents = calculations.calc_overtime_pay_minutes(overtime_min, hourly)
        else:
            undertime_min = 0
            overtime_min = 0
            day_pay_cents = calculations.weekend_pay_for_duration(duration_min, hourly, self.lunch_min)
            overtime_pay_cents = 0
        database.save_shift(self.conn, d.isoformat(), activation, end, duration_min, undertime_min, overtime_min, day_pay_cents, overtime_pay_cents, notes)
        self._draw_calendar()

    def _calculate_duration(self, act, end):
        if not act or not end: return 0
        try:
            act_dt = datetime.strptime(act, "%H:%M")
            end_dt = datetime.strptime(end, "%H:%M")
            if end_dt < act_dt: end_dt += timedelta(days=1)
            return int((end_dt - act_dt).total_seconds() / 60)
        except:
            return 0

    def _update_info_labels(self):
        # Расчёт зарплаты за периоды
        prev_month = self.cur_month - 1
        prev_year = self.cur_year
        if prev_month == 0:
            prev_month = 12
            prev_year -= 1

        # Зарплата за 16-30(31) предыдущего месяца (выплата 14-го текущего)
        last_day_prev = calendar.monthrange(prev_year, prev_month)[1]
        second_start_prev = date(prev_year, prev_month, 16).isoformat()
        second_end_prev = date(prev_year, prev_month, last_day_prev).isoformat()
        second_shifts_prev = database.list_shifts_between(self.conn, second_start_prev, second_end_prev)
        salary_second_prev = sum(cents_to_money(s[5] + s[6] or 0) for s in second_shifts_prev)  # day_pay + overtime_pay

        # Зарплата за 1-15 текущего месяца (выплата 29-го)
        first_start = date(self.cur_year, self.cur_month, 1).isoformat()
        first_end = date(self.cur_year, self.cur_month, 15).isoformat()
        first_shifts = database.list_shifts_between(self.conn, first_start, first_end)
        salary_first = sum(cents_to_money(s[5] + s[6] or 0) for s in first_shifts)

        pending = database.find_pending_overtimes(self.conn, self.cur_year, self.cur_month)
        pending_ot = sum(row[1] or 0 for row in pending)

        self.lbl_salary_second_prev.config(text=f"{salary_second_prev:.2f} руб")
        self.lbl_salary_first.config(text=f"{salary_first:.2f} руб")
        self.lbl_pending_overtime.config(text=f"Нераспределенная переработка: {format_minutes_hhmm(pending_ot)}")

        # Ожидаемый конец смены и заработок сегодня
        today_iso = self.today.isoformat()
        today_shift = database.load_shift(self.conn, today_iso)

        if today_shift and today_shift[0]:  # есть время активации
            try:
                act_time = datetime.strptime(today_shift[0], "%H:%M")
                expected = act_time + timedelta(hours=8) + timedelta(minutes=self.lunch_min)
                self.lbl_expected_end.config(text=expected.strftime("%H:%M"))

                earn_today = cents_to_money((today_shift[5] or 0) + (today_shift[6] or 0))
                self.lbl_today_earn.config(text=f"{earn_today:.2f} руб")
            except:
                self.lbl_expected_end.config(text="—")
                self.lbl_today_earn.config(text="0.00 руб")
        else:
            self.lbl_expected_end.config(text="—")
            self.lbl_today_earn.config(text="0.00 руб")

    def _start_timer(self):
        if self.after_id is not None:
            self.master.after_cancel(self.after_id)
        self._draw_calendar()
        self.after_id = self.master.after(60000, self._start_timer)

if __name__ == "__main__":
    # For testing, but use run.py
    pass