import { createContext, useContext, useState } from 'react';

const LANG_KEY = 'impulse_lang';

export function getLanguage() {
  const stored = localStorage.getItem(LANG_KEY);
  return stored === 'en' ? 'en' : 'ru';
}

export function setLanguage(lang) {
  localStorage.setItem(LANG_KEY, lang === 'en' ? 'en' : 'ru');
}

const STRINGS = {
  ru: {
    common: {
      close: 'Закрыть',
      loading: 'Загрузка…',
      chats: 'Чаты',
      memory: 'Память',
      settings: 'Настройки',
      account: 'Аккаунт',
      signOut: 'Выйти',
      newChat: 'Новый чат',
      reset: 'Сбросить',
    },
    hero: {
      title: 'О чём подумаем?',
      sub: 'Impulse запоминает то, что вы рассказываете, и умеет действовать: ставить задачи, записывать траты и показатели здоровья.',
    },
    chips: [
      'Хочу купить хлеб за 200 рублей',
      'Поставь задачу закончить отчёт до пятницы',
      'Я живу в Москве и учусь на программиста',
      'Сколько я потратил в этом месяце?',
    ],
    replyError: 'Не получилось получить ответ. Проверьте, что бэкенд запущен.',
    composer: {
      placeholder: 'Спросите что угодно…',
      messageAriaLabel: 'Сообщение',
      attachAriaLabel: 'Прикрепить файл',
      reasoningLabel: 'Размышления',
      reasoningOnTitle: 'Размышления включены — ответы медленнее, но точнее',
      reasoningOffTitle: 'Размышления выключены — ответы быстрее',
      enterHint: 'Enter — отправить, Shift+Enter — новая строка',
      sendAriaLabel: 'Отправить',
      stopAriaLabel: 'Остановить',
      disclaimer: 'Impulse помнит о вас между сессиями и может действовать: создавать задачи, записывать траты и показатели здоровья.',
    },
    chat: {
      ariaLabel: 'Переписка',
      copy: 'Скопировать',
      copied: 'Скопировано',
      code: 'Код',
      mapMiss: 'Не нашёл «{query}» на карте.',
      routeMiss: 'Не получилось построить маршрут «{query}».',
      routeTitle: 'Маршрут: {from} → {to}',
      workoutRouteTitle: 'Маршрут: {name}',
      minutesShort: 'мин',
      hoursShort: 'ч',
      kmShort: 'км',
      thinkingWords: [
        'Думаю', 'Соображаю', 'Кумекаю', 'Смекаю', 'Мозгую', 'Взвешиваю',
        'Прикидываю', 'Перевариваю', 'Вникаю', 'Копаюсь', 'Ищу мысль',
        'Собираю мысли', 'Формулирую', 'Подбираю слова', 'Настраиваюсь',
        'Ворошу идеи', 'Плету ответ', 'Колдую', 'Завариваю мысль',
        'Прощупываю', 'Ловлю нить', 'Раскручиваю', 'Разгоняюсь',
      ],
      actions: {
        create_task: 'Задача создана',
        add_expense: 'Трата записана',
        log_meal: 'Приём пищи записан',
        log_weight: 'Вес записан',
        log_workout: 'Тренировка записана',
        get_workouts: 'Тренировки',
        list_tasks: 'Список задач',
        get_expense_summary: 'Сводка трат',
        get_health_summary: 'Сводка по здоровью',
        get_weather: 'Погода',
        get_exchange_rate: 'Курс валюты',
      },
    },
    login: {
      loginTitle: 'Вход',
      registerTitle: 'Регистрация',
      username: 'Имя пользователя',
      password: 'Пароль',
      loginSubmit: 'Войти',
      registerSubmit: 'Создать аккаунт',
      toRegister: 'Нет аккаунта? Зарегистрироваться',
      toLogin: 'Уже есть аккаунт? Войти',
      otherMethods: 'Другие способы входа',
      or: 'или',
      googleError: 'Не получилось войти через Google.',
      loginError: 'Не получилось войти. Попробуйте ещё раз.',
    },
    chatList: {
      noChats: 'Пока нет ни одного чата.',
      deleteAriaLabel: 'Удалить чат',
    },
    memoryPanel: {
      types: {
        fact: 'Факт',
        preference: 'Предпочтение',
        episode: 'Событие',
        entity: 'Сущность',
        relationship: 'Отношение',
        goal: 'Цель',
        state: 'Состояние',
        procedure: 'Процедура',
      },
      aboutYou: 'Что ассистент запомнил о вас',
      dreamTooltip: 'Принудительная нормализация памяти — находит и объединяет дубликаты и противоречия среди всех фактов',
      dreamButton: 'Осмыслить',
      dreamButtonBusy: 'Обрабатываю…',
      dreamError: 'Не получилось обработать память. Попробуйте ещё раз.',
      addFactPlaceholder: 'Добавить факт о себе…',
      addButton: 'Добавить',
      addButtonBusy: 'Добавляю…',
      addError: 'Не получилось добавить. Попробуйте ещё раз.',
      searchPlaceholder: 'Поиск по памяти…',
      searchButton: 'Найти',
      searchButtonBusy: 'Ищу…',
      searchError: 'Не получилось найти. Попробуйте ещё раз.',
      noSearchResults: 'Ничего не найдено по этому запросу.',
      noMemories: 'Пока ничего — напишите что-нибудь о себе в чате или добавьте вручную.',
      deleteFactAriaLabel: 'Удалить факт',
      recentActions: 'Последние действия',
      taskLine: 'Задача: {title}',
      taskDueSuffix: ' — до {date}',
      expenseLine: 'Трата: {amount} ₽ ({category}), {date}',
      mealLine: 'Приём пищи: {name}, {kcal} ккал, {date}',
      weightLine: 'Вес: {value} кг, {date}',
    },
    settings: {
      themeSectionTitle: 'Тема',
      themeSystem: 'Системная',
      themeLight: 'Светлая',
      themeDark: 'Тёмная',
      languageSectionTitle: 'Язык',
      langRu: 'Русский',
      langEn: 'English',
      intervalsConnected: 'Подключено',
      intervalsConnectedHint: 'Ассистент может записывать тренировки и показывать маршруты из вашего аккаунта Intervals.icu.',
      disconnect: 'Отключить',
      disconnecting: 'Отключаю…',
      intervalsNotConnected: 'Не подключено',
      connect: 'Подключить',
      intervalsConnectHint: 'Вставьте личный API-ключ из intervals.icu → Settings → Developer Settings, чтобы ассистент мог записывать тренировки и показывать их маршруты на карте.',
      apiKeyPlaceholder: 'API-ключ',
      connecting: 'Подключаю…',
      connectError: 'Не получилось подключить — проверьте ключ.',
    },
  },
  en: {
    common: {
      close: 'Close',
      loading: 'Loading…',
      chats: 'Chats',
      memory: 'Memory',
      settings: 'Settings',
      account: 'Account',
      signOut: 'Sign out',
      newChat: 'New chat',
      reset: 'Reset',
    },
    hero: {
      title: "What's on your mind?",
      sub: 'Impulse remembers what you tell it and can take action: create tasks, log expenses and health metrics.',
    },
    chips: [
      'I want to buy bread for 200 rubles',
      'Set a task to finish the report by Friday',
      'I live in Moscow and I study programming',
      'How much did I spend this month?',
    ],
    replyError: "Couldn't get a reply. Check that the backend is running.",
    composer: {
      placeholder: 'Ask anything…',
      messageAriaLabel: 'Message',
      attachAriaLabel: 'Attach file',
      reasoningLabel: 'Reasoning',
      reasoningOnTitle: 'Reasoning enabled — slower but more accurate replies',
      reasoningOffTitle: 'Reasoning disabled — faster replies',
      enterHint: 'Enter to send, Shift+Enter for a new line',
      sendAriaLabel: 'Send',
      stopAriaLabel: 'Stop',
      disclaimer: 'Impulse remembers you across sessions and can take action: create tasks, log expenses and health metrics.',
    },
    chat: {
      ariaLabel: 'Conversation',
      copy: 'Copy',
      copied: 'Copied',
      code: 'Code',
      mapMiss: 'Couldn’t find "{query}" on the map.',
      routeMiss: 'Couldn’t build a route for "{query}".',
      routeTitle: 'Route: {from} → {to}',
      workoutRouteTitle: 'Route: {name}',
      minutesShort: 'min',
      hoursShort: 'h',
      kmShort: 'km',
      thinkingWords: [
        'Thinking', 'Pondering', 'Mulling', 'Reasoning', 'Weighing',
        'Considering', 'Digesting', 'Puzzling', 'Sifting', 'Searching',
        'Gathering thoughts', 'Wording it', 'Tuning in', 'Sketching it out',
        'Brewing a thought', 'Working it out', 'Chasing an idea', 'Ramping up',
      ],
      actions: {
        create_task: 'Task created',
        add_expense: 'Expense logged',
        log_meal: 'Meal logged',
        log_weight: 'Weight logged',
        log_workout: 'Workout logged',
        get_workouts: 'Workouts',
        list_tasks: 'Task list',
        get_expense_summary: 'Expense summary',
        get_health_summary: 'Health summary',
        get_weather: 'Weather',
        get_exchange_rate: 'Exchange rate',
      },
    },
    login: {
      loginTitle: 'Sign in',
      registerTitle: 'Sign up',
      username: 'Username',
      password: 'Password',
      loginSubmit: 'Sign in',
      registerSubmit: 'Create account',
      toRegister: "Don't have an account? Sign up",
      toLogin: 'Already have an account? Sign in',
      otherMethods: 'Other sign-in methods',
      or: 'or',
      googleError: "Couldn't sign in with Google.",
      loginError: "Couldn't sign in. Please try again.",
    },
    chatList: {
      noChats: 'No chats yet.',
      deleteAriaLabel: 'Delete chat',
    },
    memoryPanel: {
      types: {
        fact: 'Fact',
        preference: 'Preference',
        episode: 'Episode',
        entity: 'Entity',
        relationship: 'Relationship',
        goal: 'Goal',
        state: 'State',
        procedure: 'Procedure',
      },
      aboutYou: 'What the assistant remembers about you',
      dreamTooltip: 'Forced memory normalization — finds and merges duplicate or contradicting facts',
      dreamButton: 'Reflect',
      dreamButtonBusy: 'Processing…',
      dreamError: "Couldn't process memory. Please try again.",
      addFactPlaceholder: 'Add a fact about yourself…',
      addButton: 'Add',
      addButtonBusy: 'Adding…',
      addError: "Couldn't add it. Please try again.",
      searchPlaceholder: 'Search memory…',
      searchButton: 'Search',
      searchButtonBusy: 'Searching…',
      searchError: "Couldn't search. Please try again.",
      noSearchResults: 'Nothing found for this query.',
      noMemories: "Nothing yet — tell the assistant something about yourself in chat, or add it manually.",
      deleteFactAriaLabel: 'Delete fact',
      recentActions: 'Recent activity',
      taskLine: 'Task: {title}',
      taskDueSuffix: ' — due {date}',
      expenseLine: 'Expense: {amount} ₽ ({category}), {date}',
      mealLine: 'Meal: {name}, {kcal} kcal, {date}',
      weightLine: 'Weight: {value} kg, {date}',
    },
    settings: {
      themeSectionTitle: 'Theme',
      themeSystem: 'System',
      themeLight: 'Light',
      themeDark: 'Dark',
      languageSectionTitle: 'Language',
      langRu: 'Русский',
      langEn: 'English',
      intervalsConnected: 'Connected',
      intervalsConnectedHint: 'The assistant can log workouts and show routes from your Intervals.icu account.',
      disconnect: 'Disconnect',
      disconnecting: 'Disconnecting…',
      intervalsNotConnected: 'Not connected',
      connect: 'Connect',
      intervalsConnectHint: 'Paste your personal API key from intervals.icu → Settings → Developer Settings so the assistant can log workouts and show their routes on the map.',
      apiKeyPlaceholder: 'API key',
      connecting: 'Connecting…',
      connectError: "Couldn't connect — check the key.",
    },
  },
};

function getPath(obj, path) {
  return path.split('.').reduce((acc, part) => (acc == null ? acc : acc[part]), obj);
}

// Reads a translated value for `key` (dot-separated path into STRINGS, e.g.
// "settings.themeSystem"). Non-string values (arrays/objects, like
// thinkingWords or actions) are returned as-is; `vars` fills `{name}`
// placeholders in string results. Falls back to Russian, then to the raw
// key, so a missing translation degrades instead of crashing.
export function translate(lang, key, vars) {
  const value = getPath(STRINGS[lang], key) ?? getPath(STRINGS.ru, key) ?? key;
  if (typeof value !== 'string') return value;
  if (!vars) return value;
  return Object.entries(vars).reduce(
    (str, [k, v]) => str.replaceAll(`{${k}}`, v),
    value
  );
}

const I18nContext = createContext({
  lang: 'ru',
  t: (key, vars) => translate('ru', key, vars),
  changeLanguage: () => {},
});

export function I18nProvider({ children }) {
  const [lang, setLang] = useState(getLanguage);

  function changeLanguage(next) {
    setLanguage(next);
    setLang(next === 'en' ? 'en' : 'ru');
  }

  const value = {
    lang,
    t: (key, vars) => translate(lang, key, vars),
    changeLanguage,
  };

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  return useContext(I18nContext);
}
