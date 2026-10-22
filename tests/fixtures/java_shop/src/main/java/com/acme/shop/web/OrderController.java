package com.acme.shop.web;

import com.acme.shop.service.OrderService;
import com.acme.shop.service.Order;
import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/api/orders")
public class OrderController {
    private final OrderService orderService;

    public OrderController(OrderService orderService) {
        this.orderService = orderService;
    }

    @GetMapping("/{id}")
    public Order get(@PathVariable long id) {
        return orderService.find(id);
    }

    @PostMapping
    public Order create(@RequestBody Order order) {
        return orderService.place(order);
    }

    @RequestMapping(value = "/legacy", method = {RequestMethod.PUT, RequestMethod.PATCH})
    public void legacy() {
        helper();
    }

    private void helper() {}
}
