package com.acme.shop.service;

public interface Auditable {
    void audit(String event);
}
