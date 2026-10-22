package com.acme.shop.service;

import com.acme.shop.client.PaymentClient;
import com.acme.shop.repo.OrderRepository;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Service;

@Service
public class OrderService extends BaseService implements Auditable {
    private final OrderRepository repository;
    private final PaymentClient payments;

    public OrderService(OrderRepository repository, PaymentClient payments) {
        this.repository = repository;
        this.payments = payments;
    }

    public Order find(long id) {
        return repository.findById(id).orElseThrow();
    }

    public Order place(Order order) {
        payments.charge(order);
        audit("placed");
        return repository.save(order);
    }

    @KafkaListener(topics = "orders.retry")
    public void retry(Order order) {
        place(order);
    }

    @Override
    public void audit(String event) {
        AuditLog log = new AuditLog(event);
        log.write();
    }

    static class AuditLog {
        private final String event;
        AuditLog(String event) { this.event = event; }
        void write() {}
    }
}
