-- Виконується образом postgres лише при першому старті (порожній том).
-- Окрема БД для кожного сервісу: database per service.
CREATE DATABASE catalog;
CREATE DATABASE availability;
CREATE DATABASE booking;
CREATE DATABASE notification;
CREATE DATABASE auth;
