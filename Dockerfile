FROM ubuntu:24.04

RUN apt update
RUN apt install perl python3-elementpath python3-tqdm python3-numpy python3-regex python3-urllib3 python3-glob2 -y
RUN apt install python3 python3-pip -y

WORKDIR /tmp/

COPY *.py /tmp/
COPY *.pl /tmp/
COPY *.sql /tmp/
RUN ls /tmp/

CMD ["python3", "data.py"]
