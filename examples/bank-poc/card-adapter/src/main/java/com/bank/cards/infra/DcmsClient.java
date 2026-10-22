package com.bank.cards.infra;

import org.jpos.iso.ISOMsg;
import org.jpos.iso.channel.NACChannel;
import org.jpos.iso.packager.ISO87APackager;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class DcmsClient {
    @Value("${dcms.host}")
    private String host;

    @Value("${dcms.port}")
    private int port;

    private NACChannel channel;

    public void blockCard(String cardId) {
        try {
            channel = new NACChannel(host, port, new ISO87APackager(), null);
            ISOMsg msg = new ISOMsg();
            msg.setMTI("0302");
            msg.set(2, cardId);
            msg.set(91, "2");
            channel.connect();
            channel.send(msg);
            channel.receive();
        } catch (Exception e) {
            throw new IllegalStateException("DCMS block failed", e);
        }
    }
}
