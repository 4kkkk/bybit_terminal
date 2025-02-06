from PyQt5.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QComboBox, QLabel, QTableWidget,
                             QTableWidgetItem, QHeaderView, QMenu, QMessageBox, QDialog)
from PyQt5.QtCore import Qt, QTimer
import json

from .chart_widget import ChartWidget
from .dialogs.sl_tp_dialog import SlTpDialog
from .dialogs.multiple_tp_dialog import MultipleTpDialog
from .dialogs.order_edit_dialog import OrderEditDialog
from .dialogs.order_create_dialog import OrderCreateDialog
from .themes import DARK_THEME, LIGHT_THEME
from bybit_terminal.core.websocket_manager import WebSocketManager
from bybit_terminal.core.data_manager import DataManager
from queue import Queue
from bybit_terminal.utils.error_handler import ErrorHandler


class MainWindow(QMainWindow):
    """Главное окно терминала Bybit"""

    def __init__(self, position_manager, order_manager, theme_manager, logger, config) -> None:
        super().__init__()
        self.error_handler = ErrorHandler(logger)
        self._market_data_cache = {}
        self._cache_lifetime = 60  # время жизни кэша в секундах
        self._update_queue = Queue()
        self._widgets = {}  # Инициализируем пустым словарем

        # Инициализация менеджеров с передачей logger
        self.data_manager = DataManager(logger=logger)  # Передаем logger
        self.position_manager = position_manager
        self.order_manager = order_manager
        self.theme_manager = theme_manager
        self.logger = logger
        self.config = config
        self.session_manager = position_manager.session_manager

        # Регистрация обработчиков данных
        self.data_manager.add_listener('price', self._handle_price_update)
        self.data_manager.add_listener('positions', self._handle_positions_update)
        self.data_manager.add_listener('orders', self._handle_orders_update)
        self.data_manager.add_listener('balance', self._handle_balance_update)

        # Кэш данных
        self._last_data = {
            'price': 0,
            'positions': {},
            'orders': {},
            'balance': 0
        }
        # UI компоненты
        self.buy_button = None
        self.sell_button = None
        self.symbol_combo = None
        self.price_label = None
        self.balance_label = None
        self.chart_widget = None
        self.positions_table = None
        self.orders_table = None

        # Таймеры
        self.timer = None
        self.ws_timer = None
        self.last_price = 0

        # Флаги обновлений
        self._price_update_needed = False
        self._positions_update_needed = False
        self._orders_update_needed = False

        # Инициализация WebSocket
        self.ws_manager = WebSocketManager(
            url="wss://stream.bybit.com/v5/public/linear",
            on_message=self.on_message,
            on_error=self.on_error,
            on_close=self.on_close,
            on_open=self.on_open,
            logger=self.logger
        )

        # Настройка комбобокса с задержкой обновления
        self.symbol_combo = QComboBox()
        self.symbol_combo.setEditable(True)
        self.symbol_combo.setInsertPolicy(QComboBox.NoInsert)
        self.symbol_combo.setMaxVisibleItems(10)

        # Создаем таймер для задержки фильтрации
        self.filter_timer = QTimer()
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(300)  # 300мс задержка
        self.filter_timer.timeout.connect(self.perform_filter)

        # Подключаем сигналы
        self.symbol_combo.lineEdit().textChanged.connect(self.start_filter_timer)
        self.symbol_combo.activated.connect(self.on_symbol_selected)

        # Инициализация UI и настроек
        self.init_ui()

        # После создания всех компонентов регистрируем виджеты
        self._widgets = {
            'price': self.price_label,
            'positions': self.positions_table,
            'orders': self.orders_table,
            'balance': self.balance_label,
            'chart': self.chart_widget
        }

        self.load_window_settings()
        self.setup_auto_refresh()
        self.ws_manager.start()
    def _handle_price_update(self, price):
        """Обработка обновления цены"""
        self.price_label.setText(f"Цена: {price:.4f} USDT")
        self.chart_widget.update_price(price)

    def _handle_positions_update(self, positions):
        """Обработка обновления позиций"""
        self.update_positions_table(positions)
        self.update_chart_positions()

    def _handle_orders_update(self):
        """Обработка обновления ордеров"""
        self.update_orders_table()
        self.update_chart_positions()

    def _handle_balance_update(self, balance):
        """Обработка обновления баланса"""
        self.balance_label.setText(f"Баланс: {balance} USDT")

    def setup_auto_refresh(self) -> None:
        """Настройка автоматического обновления данных"""
        self.timer = QTimer()
        self.timer.setInterval(2000)  # 2 секунды
        self.timer.timeout.connect(self._process_updates)
        self.timer.timeout.connect(self.update_balance)
        self.timer.start()

    def _process_updates(self):
        """Пакетная обработка обновлений"""
        if self._update_queue.empty():
            return

        updates = {}
        while not self._update_queue.empty():
            data = self._update_queue.get()
            updates.update(data)

        self._update_ui(updates)

    def _update_ui(self, updates):
        """Эффективное обновление UI"""
        for widget_id, data in updates.items():
            if self._last_data.get(widget_id) != data:
                if widget_id in self._widgets:
                    if widget_id == 'price':
                        self._handle_price_update(data)
                    elif widget_id == 'positions':
                        self._handle_positions_update(data)
                    elif widget_id == 'orders':
                        self._handle_orders_update()
                    elif widget_id == 'balance':
                        self._handle_balance_update(data)

                self._last_data[widget_id] = data

    def update_balance(self):
        """
        Обновление отображения баланса
        """
        try:
            balance = self.session_manager.get_wallet_balance()
            if balance:
                self.balance_label.setText(f"Баланс: {balance:.2f} USDT")
        except Exception as e:
            self.logger.error(f"Ошибка при обновлении баланса: {str(e)}")

    def update_positions_table(self, positions):
        """
        Обновление таблицы позиций используя PositionManager
        """
        try:
            positions = self.position_manager.get_positions()
            self.positions_table.setRowCount(0)

            for position in positions:
                if float(position['size']) == 0:
                    continue

                row = self.positions_table.rowCount()
                self.positions_table.insertRow(row)

                # Заполнение данных из position_manager
                items = [
                    QTableWidgetItem(position['symbol']),
                    QTableWidgetItem(position['side']),
                    QTableWidgetItem(str(position['size'])),
                    QTableWidgetItem(str(position['avgPrice'])),
                    QTableWidgetItem(str(position['markPrice'])),
                    QTableWidgetItem(f"{float(position['unrealisedPnl']):.2f}"),
                    QTableWidgetItem(f"{float(position['ROE']):.2f}%")
                ]

                items[0].setData(Qt.UserRole, position)

                for col, item in enumerate(items):
                    item.setTextAlignment(Qt.AlignCenter)
                    self.positions_table.setItem(row, col, item)

        except Exception as e:
            self.logger.error(f"Ошибка обновления таблицы позиций: {str(e)}")

    def update_orders_table(self):
        """
        Обновление таблицы ордеров используя OrderManager
        """
        try:
            orders = self.order_manager.get_orders()
            self.orders_table.setRowCount(0)

            for order in orders:
                row = self.orders_table.rowCount()
                self.orders_table.insertRow(row)

                # Заполнение данных из order_manager
                items = [
                    QTableWidgetItem(order['symbol']),
                    QTableWidgetItem(order['side']),
                    QTableWidgetItem(order['orderType']),
                    QTableWidgetItem(str(order['price'])),
                    QTableWidgetItem(str(order['qty'])),
                    QTableWidgetItem(str(order['profit'])),
                    QTableWidgetItem(order['status'])
                ]

                for col, item in enumerate(items):
                    item.setTextAlignment(Qt.AlignCenter)
                    self.orders_table.setItem(row, col, item)

        except Exception as e:
            self.logger.error(f"Ошибка обновления таблицы ордеров: {str(e)}")

    def init_ui(self) -> None:
        """Инициализация пользовательского интерфейса"""
        self.setWindowTitle('Bybit Terminal')
        self.setGeometry(100, 100, 1200, 800)

        # Создание центрального виджета
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Создание верхней панели
        top_panel = self._create_top_panel()
        main_layout.addLayout(top_panel)

        # Создание графика
        self.chart_widget = ChartWidget()
        self.chart_widget.setMinimumHeight(600)
        main_layout.addWidget(self.chart_widget)

        # Создание таблиц
        self._create_tables()
        main_layout.addWidget(self.positions_table)
        main_layout.addWidget(self.orders_table)

        # Подключение обработчиков событий
        self.positions_table.clicked.connect(self.handle_position_selection)

        # Настройка таймера обновления UI
        self.update_timer = QTimer()
        self.update_timer.setInterval(2000)  # 2 секунды
        self.update_timer.timeout.connect(self.update_ui)
        self.update_timer.start()

        # Загрузка символов и применение темы
        self.setup_symbol_search()
        self.apply_theme()

    def _create_top_panel(self) -> QHBoxLayout:
        """Создание верхней панели интерфейса"""
        top_panel = QHBoxLayout()

        # Кнопка переключения темы
        theme_button = QPushButton('🌓')
        theme_button.setFixedWidth(30)
        theme_button.clicked.connect(self.toggle_theme)

        # Создание основных элементов управления
        self.buy_button = QPushButton('Buy')
        self.sell_button = QPushButton('Sell')
        self.symbol_combo = QComboBox()
        self.symbol_combo.setEditable(True)
        self.price_label = QLabel('0 USDT')
        self.balance_label = QLabel('Баланс: 0 USDT')

        # Добавляем новые кнопки управления позицией
        self.close_position_button = QPushButton('Закрыть позицию')
        self.edit_sl_tp_button = QPushButton('SL/TP')
        self.multiple_tp_button = QPushButton('Multiple TP')
        self.position_info_label = QLabel('Нет выбранной позиции')

        # Устанавливаем начальное состояние кнопок
        self.close_position_button.setEnabled(False)
        self.edit_sl_tp_button.setEnabled(False)
        self.multiple_tp_button.setEnabled(False)

        # Подключение сигналов
        self.buy_button.clicked.connect(lambda: self.show_order_dialog('Buy'))
        self.sell_button.clicked.connect(lambda: self.show_order_dialog('Sell'))
        self.symbol_combo.currentTextChanged.connect(self.on_symbol_selected)
        self.close_position_button.clicked.connect(lambda: self.close_position(self.selected_position['symbol'], float(
            self.selected_position['size'])) if hasattr(self, 'selected_position') else None)
        self.edit_sl_tp_button.clicked.connect(
            lambda: self.edit_position_sl_tp(self.selected_position['symbol']) if hasattr(self,
                                                                                          'selected_position') else None)
        self.multiple_tp_button.clicked.connect(lambda: self.show_multiple_tp_window(self.selected_position['symbol'],
                                                                                     float(self.selected_position[
                                                                                               'size'])) if hasattr(
            self, 'selected_position') else None)

        # Добавление элементов на панель
        top_panel.addWidget(theme_button)
        top_panel.addWidget(self.buy_button)
        top_panel.addWidget(self.sell_button)
        top_panel.addWidget(self.symbol_combo)
        top_panel.addWidget(self.price_label)
        top_panel.addWidget(self.balance_label)
        top_panel.addWidget(self.position_info_label)
        top_panel.addWidget(self.close_position_button)
        top_panel.addWidget(self.edit_sl_tp_button)
        top_panel.addWidget(self.multiple_tp_button)
        top_panel.addStretch()

        return top_panel

    def _create_tables(self) -> None:
        """Создание и настройка таблиц позиций и ордеров"""
        # Таблица позиций
        self.positions_table = QTableWidget()
        self.positions_table.setMaximumHeight(150)
        self.setup_positions_table()

        # Таблица ордеров
        self.orders_table = QTableWidget()
        self.orders_table.setMaximumHeight(150)
        self.setup_orders_table()

    def setup_positions_table(self) -> None:
        """Настройка таблицы позиций"""
        columns = ["Символ", "Сторона", "Размер", "Цена входа", "Текущая цена", "P&L", "ROE"]
        self.positions_table.setColumnCount(len(columns))
        self.positions_table.setHorizontalHeaderLabels(columns)
        self.positions_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.positions_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.positions_table.customContextMenuRequested.connect(self.show_position_menu)
        self.positions_table.doubleClicked.connect(self.edit_position)
        self.positions_table.clicked.connect(self.on_position_selected)

    def setup_orders_table(self) -> None:
        """Настройка таблицы ордеров"""
        columns = ["Символ", "Сторона", "Тип", "Цена", "Размер", "Прибыль", "Статус"]
        self.orders_table.setColumnCount(len(columns))
        self.orders_table.setHorizontalHeaderLabels(columns)
        self.orders_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.orders_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.orders_table.customContextMenuRequested.connect(self.show_order_menu)

    def on_message(self, ws, message, *args):
        """
        Обработка сообщений WebSocket через WebSocketManager
        """
        self.ws_manager.process_message(message)

    def on_error(self, _unused, error) -> None:
        """Обработка ошибок WebSocket"""
        self.logger.error(f"Ошибка WS: {str(error)}")

    def on_close(self, _ws, _close_status_code, _close_msg) -> None:
        """Обработка закрытия соединения WebSocket"""
        self.logger.info("WS соединение закрыто")

    def on_open(self, ws) -> None:
        """Обработка открытия соединения WebSocket"""
        self.logger.info("WS соединение открыто")
        subscribe_message = {
            "op": "subscribe",
            "args": [f"tickers.{self.symbol_combo.currentText()}"]
        }
        ws.send(json.dumps(subscribe_message))
    def show_position_menu(self, pos) -> None:
        """Отображение контекстного меню позиции"""
        menu = QMenu(self)
        close_action = menu.addAction("Закрыть позицию")
        edit_action = menu.addAction("Установить SL/TP")
        multiple_tp_action = menu.addAction("Множественные TP")

        selected_items = self.positions_table.selectedItems()
        if not selected_items:
            return

        row = selected_items[0].row()
        symbol = self.positions_table.item(row, 0).text()
        size = float(self.positions_table.item(row, 2).text())

        close_action.triggered.connect(lambda: self.close_position(symbol, size))
        edit_action.triggered.connect(lambda: self.edit_position_sl_tp(symbol))
        multiple_tp_action.triggered.connect(lambda: self.show_multiple_tp_window(symbol, size))

        menu.exec_(self.positions_table.viewport().mapToGlobal(pos))

    def close_position(self, symbol: str, size: float) -> None:
        """Закрытие позиции"""
        try:
            response = self.position_manager.close_position(symbol, size)
            if response and response.get('retCode') == 0:
                self.logger.info(f"Позиция {symbol} успешно закрыта")
                self._positions_update_needed = True
                self._orders_update_needed = True
                self.show_message("Успех", f"Позиция {symbol} закрыта")
            else:
                error_msg = "Неизвестная ошибка" if not response else response.get('retMsg', 'Неизвестная ошибка')
                self.logger.error(f"Ошибка закрытия позиции: {error_msg}")
                self.show_message("Ошибка", f"Не удалось закрыть позицию: {error_msg}")
        except Exception as e:
            self.logger.error(f"Ошибка при закрытии позиции: {str(e)}")
            self.show_message("Ошибка", f"Не удалось закрыть позицию: {str(e)}")

    def edit_position(self, position):
        """Редактирование TP/SL для позиции"""
        dialog = SlTpDialog(
            parent=self,
            symbol=position['symbol'],
            side=position['side'],
            entry_price=float(position.get('avgPrice', 0)),
            size=float(position['size']),
            current_tp=position.get('takeProfit'),
            current_sl=position.get('stopLoss')
        )

        if dialog.exec_():
            tp, sl = dialog.get_values()
            try:
                params = {
                    "category": "linear",
                    "symbol": position['symbol'],
                    "positionIdx": 0
                }
                if tp:
                    params["takeProfit"] = str(tp)
                if sl:
                    params["stopLoss"] = str(sl)

                response = self.session_manager.get_session().set_trading_stop(**params)
                self.error_handler.handle_bybit_error(response)
                self.update_positions()
            except Exception as e:
                self.logger.error(f"Ошибка при установке TP/SL: {str(e)}")

    def show_multiple_tp_window(self, symbol: str, size: float) -> None:
        """Отображение окна множественных Take Profit"""
        try:
            dialog = MultipleTpDialog(symbol, size, self.session_manager, self)
            if dialog.exec_() == QDialog.Accepted:
                self.refresh_data()
        except Exception as e:
            self.logger.error(f"Ошибка при установке множественных TP: {str(e)}")
            QMessageBox.critical(self, "Ошибка", str(e))

    def show_order_menu(self, pos) -> None:
        """Отображение контекстного меню ордера"""
        menu = QMenu(self)
        cancel_action = menu.addAction("Отменить ордер")
        edit_action = menu.addAction("Изменить ордер")

        selected_items = self.orders_table.selectedItems()
        if not selected_items:
            return

        row = selected_items[0].row()
        symbol = self.orders_table.item(row, 0).text()
        order_id = self.orders_table.item(row, 0).data(Qt.UserRole)

        cancel_action.triggered.connect(lambda: self.cancel_order(symbol, order_id))
        edit_action.triggered.connect(lambda: self.edit_order(symbol, order_id))

        menu.exec_(self.orders_table.viewport().mapToGlobal(pos))
    def apply_theme(self) -> None:
        """Применение текущей темы к интерфейсу"""
        theme = DARK_THEME if self.theme_manager.current_theme == "dark" else LIGHT_THEME
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {theme['background']};
                color: {theme['text']};
            }}
            QPushButton {{
                background-color: {theme['button']};
                color: {theme['button_text']};
                border: none;
                padding: 5px 15px;
                border-radius: 3px;
            }}
            QPushButton:hover {{
                background-color: {theme['button']}dd;
            }}
            QComboBox {{
                background-color: {theme['button']};
                color: {theme['button_text']};
                border: none;
                padding: 5px;
                border-radius: 3px;
            }}
            QTableWidget {{
                background-color: {theme['background']};
                color: {theme['table_text']};
                gridline-color: {theme['table_header']};
            }}
            QTableWidget::item {{
                padding: 5px;
            }}
            QHeaderView::section {{
                background-color: {theme['table_header']};
                color: {theme['text']};
                padding: 5px;
                border: none;
            }}
            QLabel {{
                color: {theme['text']};
            }}
        """)
        self.chart_widget.set_theme('dark' if self.theme_manager.current_theme == "dark" else 'light')

    def toggle_theme(self) -> None:
        """Переключение темы оформления"""
        self.theme_manager.toggle_theme()
        self.apply_theme()

    def save_window_settings(self) -> None:
        """Сохранение настроек окна"""
        settings = {
            'window_geometry': {
                'x': self.geometry().x(),
                'y': self.geometry().y(),
                'width': self.geometry().width(),
                'height': self.geometry().height()
            },
            'theme': self.theme_manager.current_theme,
            'symbol': self.symbol_combo.currentText()
        }
        self.config.save_window_settings(settings)
    def load_window_settings(self) -> None:
        """Загрузка настроек окна"""
        settings = self.config.load_window_settings()
        if settings:
            if 'window_geometry' in settings:
                geo = settings['window_geometry']
                self.setGeometry(geo['x'], geo['y'], geo['width'], geo['height'])
            if 'theme' in settings:
                self.theme_manager.current_theme = settings['theme']
                self.apply_theme()
            if 'symbol' in settings:
                index = self.symbol_combo.findText(settings['symbol'])
                if index >= 0:
                    self.symbol_combo.setCurrentIndex(index)

    def closeEvent(self, event) -> None:
        """Обработка события закрытия окна"""
        self.save_window_settings()
        event.accept()

    def show_message(self, title: str, message: str) -> None:
        """Отображение информационного сообщения"""
        QMessageBox.information(self, title, message)



    def start_filter_timer(self, text):
        """Запуск таймера фильтрации"""
        self.filter_timer.start()

    def perform_filter(self):
        """Выполнение фильтрации после задержки"""
        text = self.symbol_combo.lineEdit().text().upper()
        self.symbol_combo.clear()
        filtered_symbols = [symbol for symbol in self.all_symbols
                            if text in symbol]
        self.symbol_combo.addItems(filtered_symbols)
        if filtered_symbols:
            self.symbol_combo.showPopup()



    def update_chart_positions(self) -> None:
        """Обновление позиций на графике"""
        try:
            positions = self.position_manager.get_positions()
            self.chart_widget.update_positions_and_orders(positions, self.order_manager.get_orders())

        except Exception as e:
            self.logger.error(f"Ошибка обновления позиций на графике: {str(e)}")

    def edit_order(self, symbol: str, order_id: str) -> None:
        """Редактирование ордера"""
        try:
            order = self.order_manager.get_order(symbol, order_id)
            if order:
                dialog = OrderEditDialog(order, self.session_manager, self)
                if dialog.exec_() == QDialog.Accepted:
                    self.refresh_data()
        except Exception as e:
            self.logger.error(f"Ошибка при редактировании ордера: {str(e)}")
            QMessageBox.critical(self, "Ошибка", str(e))

    def show_order_dialog(self, side: str) -> None:
        """Отображение диалога создания ордера"""
        try:
            symbol = self.symbol_combo.currentText()
            dialog = OrderCreateDialog(symbol, side, self.session_manager, self)
            if dialog.exec_() == QDialog.Accepted:
                self.refresh_data()
        except Exception as e:
            self.logger.error(f"Ошибка при создании ордера: {str(e)}")
            QMessageBox.critical(self, "Ошибка", str(e))



    def edit_position(self, index):
        """
        Обработчик двойного клика по позиции для её редактирования
        Args:
            index: индекс выбранной строки в таблице
        """
        row = index.row()
        position = self.positions_table.item(row, 0).data(Qt.UserRole)
        if not position:
            return

        dialog = SlTpDialog(
            parent=self,
            symbol=position['symbol'],
            side=position['side'],
            entry_price=float(position.get('avgPrice', 0)),
            size=float(position['size']),
            current_tp=position.get('takeProfit'),
            current_sl=position.get('stopLoss')
        )

        if dialog.exec_():
            tp, sl = dialog.get_values()
            try:
                params = {
                    "category": "linear",
                    "symbol": position['symbol'],
                    "positionIdx": 0
                }
                if tp:
                    params["takeProfit"] = str(tp)
                if sl:
                    params["stopLoss"] = str(sl)

                response = self.session_manager.get_session().set_trading_stop(**params)
                self.error_handler.handle_bybit_error(response)
                self.update_positions()
            except Exception as e:
                self.logger.error(f"Ошибка при установке TP/SL: {str(e)}")



    def update_position_buttons_state(self):
        """Обновление состояния кнопок управления позицией"""
        has_position = hasattr(self, 'selected_position') and self.selected_position is not None
        self.close_position_button.setEnabled(has_position)
        self.edit_sl_tp_button.setEnabled(has_position)
        self.multiple_tp_button.setEnabled(has_position)

    def update_position_details(self):
        """Обновление отображения деталей выбранной позиции"""
        if hasattr(self, 'selected_position') and self.selected_position:
            position = self.selected_position
            entry_price = position.get('avgPrice', '0')
            self.position_info_label.setText(
                f"Позиция: {position['symbol']} | "
                f"Сторона: {position['side']} | "
                f"Размер: {position['size']} | "
                f"Цена входа: {entry_price}"
            )
        else:
            self.position_info_label.setText("Нет выбранной позиции")





    def load_symbols(self):
        """Загрузка списка торговых пар"""
        try:
            self.all_symbols = self.session_manager.get_symbols()
            self.symbol_combo.clear()
            self.symbol_combo.addItems(self.all_symbols)

            # Установка BTCUSDT.P по умолчанию
            default_symbol = self.config.get('default_symbol', 'BTCUSDT.P')
            index = self.symbol_combo.findText(default_symbol)
            if index >= 0:
                self.symbol_combo.setCurrentIndex(index)
                self.on_symbol_selected(index)
        except Exception as e:
            self.logger.error(f"Ошибка загрузки символов: {str(e)}")

    def on_symbol_selected(self, symbol):
        """Обработчик выбора символа из списка"""
        if not symbol:
            return

        self.current_symbol = symbol
        self.config.save_window_settings({'symbol': symbol})
        self.data_manager.change_symbol(symbol)
        self.position_manager.change_symbol(symbol)
        self.order_manager.change_symbol(symbol)
        self.update_position_info()
        self.update_orders_table()
        self.logger.info(f"Символ изменен на {symbol}")

    def setup_symbol_search(self) -> None:
        """Настройка быстрого поиска торговых пар"""
        self.available_symbols = []

        # Создаем таймер для отложенной фильтрации
        self.filter_timer = QTimer()
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(300)

        def load_symbols():
            """Загрузка всех торговых пар"""
            try:
                response = self.session_manager.get_instruments_info(category="linear")
                if response and "list" in response:
                    self.available_symbols = [item["symbol"] for item in response["list"]]
                    self.available_symbols.sort()

                    self.symbol_combo.clear()
                    self.symbol_combo.addItems(self.available_symbols)

                    # Восстанавливаем сохраненный символ
                    current_symbol = self.config.get_window_settings().get('symbol', self.config.default_symbol)
                    index = self.symbol_combo.findText(current_symbol)
                    if index >= 0:
                        self.symbol_combo.setCurrentIndex(index)

                    self.logger.info("Список торговых пар успешно загружен")
            except Exception as e:
                self.logger.error(f"Ошибка загрузки торговых пар: {str(e)}")

        def filter_symbols():
            """Быстрая фильтрация символов"""
            search_text = self.symbol_combo.lineEdit().text().upper()
            filtered_symbols = [
                symbol for symbol in self.available_symbols
                if search_text in symbol
            ]
            self.symbol_combo.clear()
            self.symbol_combo.addItems(filtered_symbols)
            if filtered_symbols:
                self.symbol_combo.showPopup()

        # Настройка комбобокса
        self.symbol_combo.setEditable(True)
        self.symbol_combo.setInsertPolicy(QComboBox.NoInsert)
        self.symbol_combo.setMaxVisibleItems(10)

        # Подключаем обработчики
        self.filter_timer.timeout.connect(filter_symbols)
        self.symbol_combo.lineEdit().textChanged.connect(
            lambda: self.filter_timer.start()
        )

        # Загружаем символы при старте
        load_symbols()

    def update_ui(self) -> None:
        """
        Централизованное обновление UI через DataManager
        """
        try:
            # Получаем все обновления через data_manager
            self.data_manager.update_all()

            # Обновляем компоненты UI
            self.update_positions_table(self.position_manager.get_positions())
            self.update_orders_table()
            self.update_chart_positions()

            # Обновляем баланс
            balance = self.session_manager.get_wallet_balance()
            if balance:
                self.balance_label.setText(f"Баланс: {balance:.2f} USDT")

        except Exception as e:
            self.logger.error(f"Ошибка обновления UI: {str(e)}")

    def handle_position_selection(self, index) -> None:
        """Обработка выбора позиции"""
        row = index.row()
        if row < 0:
            return

        try:
            # Получаем данные позиции
            symbol = self.positions_table.item(row, 0).text()
            position = self.position_manager.get_position(symbol)

            if position:
                self.selected_position = position

                # Обновляем UI
                self.update_position_buttons_state()
                self.position_info_label.setText(
                    f"Позиция: {position['symbol']} | "
                    f"Сторона: {position['side']} | "
                    f"Размер: {position['size']} | "
                    f"Цена входа: {position.get('avgPrice', '0')}"
                )
            else:
                self.reset_position_selection()

        except Exception as e:
            self.logger.error(f"Ошибка при выборе позиции: {str(e)}")
            self.reset_position_selection()

    def reset_position_selection(self) -> None:
        """Сброс выбранной позиции"""
        self.selected_position = None
        self.position_info_label.setText("Нет выбранной позиции")
        self.update_position_buttons_state()



    def edit_position_sl_tp(self, symbol: str) -> None:
        """
        Вызов диалога редактирования Stop Loss и Take Profit
        Args:
            symbol: Торговый символ
        """
        try:
            position = self.position_manager.get_position(symbol)
            if position:
                dialog = SlTpDialog(
                    parent=self,
                    symbol=position['symbol'],
                    side=position['side'],
                    entry_price=float(position.get('avgPrice', 0)),
                    size=float(position['size']),
                    current_tp=position.get('takeProfit'),
                    current_sl=position.get('stopLoss')
                )

                if dialog.exec_():
                    self.update_positions_table(self.position_manager.get_positions())
                    self.update_chart_positions()

        except Exception as e:
            self.logger.error(f"Ошибка при открытии диалога SL/TP: {str(e)}")
